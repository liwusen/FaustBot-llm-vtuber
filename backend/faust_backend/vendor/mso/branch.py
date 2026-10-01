"""Branching for hybrid backbones (Qwen3.5 and any model with linear-attention layers).

A block-diagonal attention mask keeps options apart only in softmax layers; a recurrent
(linear-attention) layer carries state along the sequence no matter what the mask says, so on
such backbones the options leak into each other (measured: option representations differ by
4-17 across orders on Qwen3.5). This module restores exact isolation the other way round:

    prefix  = chat template head + image tokens (+ the instruction when a single question is
              asked)                                          -> ONE forward, cache kept
    suffix  = [instruction + assistant prompt +] one option    -> every (question, option) pair
              is a row of ONE batched forward that continues a copy of the expanded cache

Every row starts from the same state and never sees another option, so the answer cannot depend
on option order (measured 0.0); all questions of a record ride the same prefix (packing for
free); and a row's tokens and rope positions are exactly those of the full sequence, so the
result equals a plain forward of prefix+row (measured max |du| 4e-5 on Qwen3.5-0.8B, 1e-4 on
Qwen3-VL-4B). Costs: one prefix pass + one short batched pass (Qwen3.5-0.8B: 8 options, 54 ms).

Training works the same way: the prefix graph is shared and `repeat_interleave` is
differentiable. Rows are processed in chunks (MSO_BRANCH_CHUNK, default 64) against a fresh
copy of the prefix cache each time, so the prefix is never re-encoded.
"""
import copy
import os

import torch
import torch.utils.checkpoint

_TEXT_OPEN = "<|im_start|>"
N_FEAT = 6
_PIX_KEYS = ("pixel_values", "image_grid_thw", "pixel_values_videos", "video_grid_thw", "mm_token_type_ids")


def is_hybrid(model):
    cfg = getattr(model, "config", None)
    tc = getattr(cfg, "text_config", cfg)
    lt = getattr(tc, "layer_types", None)
    return bool(lt) and any("linear" in str(t) for t in lt)


def _rep(v, K):
    if torch.is_tensor(v) and v.dim() >= 1:
        return v.repeat_interleave(K, dim=0)
    if isinstance(v, list):
        return [_rep(x, K) for x in v]
    if isinstance(v, tuple):
        return tuple(_rep(x, K) for x in v)
    if isinstance(v, dict):
        return {k: _rep(x, K) for k, x in v.items()}
    return v


def expand_cache(cache, K):
    """a copy of `cache` with every tensor repeated K times along the batch axis (KV caches and
    recurrent states). The original stays intact, so it can be expanded again for the next chunk."""
    new = copy.copy(cache)
    layers = []
    for layer in cache.layers:
        l2 = copy.copy(layer)
        for k, v in list(vars(layer).items()):
            setattr(l2, k, _rep(v, K))
        layers.append(l2)
    new.layers = layers
    return new


def split_prompt(prompt):
    """chat-template text -> (prefix up to and including the image tokens, the rest)."""
    tail = "<|vision_end|>"
    i = prompt.rfind(tail)
    if i < 0:
        j = prompt.find(_TEXT_OPEN + "user\n")
        j = (j + len(_TEXT_OPEN + "user\n")) if j >= 0 else 0
        return prompt[:j], prompt[j:]
    i += len(tail)
    return prompt[:i], prompt[i:]


def prefix_inputs(enc, L):
    """the prefix encoding (ids[:L] + pixel tensors) cut out of a single-question or packed encoding"""
    ids = enc["input_ids"][:, :L]
    p = {"input_ids": ids, "attention_mask": torch.ones_like(ids)}
    for k in _PIX_KEYS:
        if k in enc:
            p[k] = enc[k][:, :L] if k == "mm_token_type_ids" else enc[k]
    return p


def prefix_positions(mso_wrapper, penc):
    """[3, 1, L] mrope positions of the prefix from the model's own get_rope_index (None if n/a)"""
    return mso_wrapper.rope_positions(penc, None, False)


def prefix_pos_start(mso_wrapper, penc):
    """the rope position the first suffix token gets in a full sequence: max prefix position + 1"""
    pos = prefix_positions(mso_wrapper, penc)
    if pos is None:
        return int(penc["input_ids"].shape[1])
    return int(pos.max()) + 1


def rows_from_ids(ids, L, opens, closes, k=None):
    """token ids of ONE single-question sequence (prefix + instruction + K option blocks, the
    layout the Collator produces), the prefix length L and the option spans -> K rows, each the
    shared part ids[L:opens[0]] followed by one option block ids[o:c+1]. L must be < opens[0]
    so that every row keeps the token before its option (that is where zq is read)."""
    ids = list(ids)
    if not opens or not closes:
        return [ids[L:]]
    head = ids[L:opens[0]]
    pairs = [(o, c) for o, c in zip(opens, closes) if c >= o]
    if k is not None:
        pairs = pairs[:k]
    return [head + ids[o:c + 1] for o, c in pairs]


def _marks(r, open_ids, close_ids):
    """(index of the first open marker, index of the last token of the last close marker,
    indices of the option text tokens) inside one row"""
    ol, cl = len(open_ids), len(close_ids)
    opens = [k for k in range(len(r) - ol + 1) if r[k:k + ol] == open_ids]
    closes = [k for k in range(len(r) - cl + 1) if r[k:k + cl] == close_ids]
    o_ = opens[0] if opens else 0
    c_ = (closes[-1] + cl - 1) if closes else len(r) - 1
    return o_, c_, list(range(o_ + ol, c_ - cl + 1))


def branch_forward(backbone, penc, rows, dev, open_ids, close_ids, lm_head=None, prefix_pos=None, pos_start=None,
                   groups=None, chunk=None, pad_id=0, mrope_axes=3):
    """penc: prefix encoding (input_ids [1, L], pixel tensors); rows: token-id lists that continue
    the prefix; groups: lists of row indices that form one question (the K-way softmax feature
    is taken inside a group). prefix_pos: [3, 1, L] positions of the prefix (prefix_positions);
    pos_start: rope position of the first suffix token (default: max prefix position + 1).
      -> u [R, D]   hidden state at each row's closing option marker
         zq [R, D]  hidden state just before each row's opening option marker
         feats [R, 6] backbone log-prob features of the option text (lm_head given) else None"""
    R = len(rows)
    prefix_ids = penc["input_ids"]
    L = int(prefix_ids.shape[1])
    kw = {"input_ids": prefix_ids, "attention_mask": torch.ones_like(prefix_ids), "use_cache": True}
    kw.update({k: v for k, v in penc.items() if k in _PIX_KEYS})
    if prefix_pos is not None:
        kw["position_ids"] = prefix_pos
    out = backbone(**kw, logits_to_keep=1)
    cache0 = out.past_key_values
    if pos_start is None:
        pos_start = (int(prefix_pos.max()) + 1) if prefix_pos is not None else L
    p0 = int(pos_start)
    chunk = chunk or int(os.environ.get("MSO_BRANCH_CHUNK", "64"))
    us, zqs, sums_l, cnts_l, first_l = [], [], [], [], []
    for s in range(0, R, chunk):
        sub = rows[s:s + chunk]
        Rc = len(sub)
        n = max(len(r) for r in sub)
        ids = torch.full((Rc, n), pad_id, device=dev, dtype=torch.long)
        att = torch.zeros((Rc, n), dtype=torch.long, device=dev)
        for i, r in enumerate(sub):
            ids[i, :len(r)] = torch.tensor(r, device=dev)
            att[i, :len(r)] = 1
        full_att = torch.cat([torch.ones((Rc, L), dtype=torch.long, device=dev), att], dim=1)
        pos = torch.arange(p0, p0 + n, device=dev)[None, None, :].expand(mrope_axes, Rc, n).contiguous()
        cache_pos = torch.arange(L, L + n, device=dev)

        def _chunk_h(ids_, att_, pos_, cpos_):
            o_ = backbone(input_ids=ids_, attention_mask=att_, past_key_values=expand_cache(cache0, ids_.shape[0]), use_cache=True,
                          output_hidden_states=True, cache_position=cpos_, position_ids=pos_, logits_to_keep=1)
            return o_.hidden_states[-1]

        if torch.is_grad_enabled() and os.environ.get("MSO_BRANCH_CKPT", "0") == "1":
            # recompute this chunk in backward instead of keeping its activations (memory ~ one chunk, not all rows)
            h = torch.utils.checkpoint.checkpoint(_chunk_h, ids, full_att, pos, cache_pos, use_reentrant=False)
        else:
            h = _chunk_h(ids, full_att, pos, cache_pos)               # [Rc, n, D]
        marks = [_marks(r, open_ids, close_ids) for r in sub]
        ar = torch.arange(Rc, device=dev)
        u = h[ar, torch.tensor([m[1] for m in marks], device=dev)]
        zq = h[ar, torch.tensor([max(0, m[0] - 1) for m in marks], device=dev)]
        us.append(u)
        zqs.append(zq)
        if lm_head is not None:
            pred_i, pred_t, tgt_tok, owner, firsts = [], [], [], [], []
            for i, (r, (o_, c_, inner)) in enumerate(zip(sub, marks)):
                firsts.append(r[inner[0]] if inner else -1)
                for t in inner:
                    pred_i.append(i)
                    pred_t.append(t - 1)
                    tgt_tok.append(r[t])
                    owner.append(i)
            sums = torch.zeros(Rc, device=dev)
            cnts = torch.zeros(Rc, device=dev)
            if pred_i:
                hp = h[torch.tensor(pred_i, device=dev), torch.tensor(pred_t, device=dev)]
                lp = torch.log_softmax(lm_head(hp).float(), dim=-1)
                picked = lp.gather(1, torch.tensor(tgt_tok, device=dev)[:, None]).squeeze(1)
                own = torch.tensor(owner, device=dev)
                sums = sums.index_add(0, own, picked)
                cnts = cnts.index_add(0, own, torch.ones_like(picked))
            first = torch.zeros(Rc, device=dev)
            fi = [i for i, f in enumerate(firsts) if f >= 0]
            if fi:
                lq = torch.log_softmax(lm_head(zq[fi]).float(), dim=-1)
                first[fi] = lq[torch.arange(len(fi), device=dev), torch.tensor([firsts[i] for i in fi], device=dev)]
            sums_l.append(sums)
            cnts_l.append(cnts)
            first_l.append(first)
    u, zq = torch.cat(us), torch.cat(zqs)
    feats = None
    if lm_head is not None:
        sums, cnts, first = torch.cat(sums_l), torch.cat(cnts_l), torch.cat(first_l)
        valid = cnts > 0
        feats = torch.zeros(R, N_FEAT, device=dev, dtype=torch.float32)
        masked = torch.where(valid, sums, torch.full_like(sums, -1e4))
        dist = torch.zeros(R, device=dev)
        for g in (groups or [list(range(R))]):
            gi = torch.tensor(g, device=dev)
            dist[gi] = torch.log_softmax(masked[gi], dim=0)
        feats[:, 0] = sums / 10.0
        feats[:, 1] = torch.where(valid, sums / cnts.clamp(min=1), torch.zeros_like(sums))
        feats[:, 2] = cnts / 10.0
        feats[:, 3] = dist
        feats[:, 4] = first
        feats[:, 5] = valid.float()
    return {"u": u, "zq": zq, "feats": feats}


def branch_questions(backbone, penc, qrows, dev, open_ids, close_ids, lm_head=None, prefix_pos=None, chunk=None, pad_id=0):
    """qrows: per question, its rows -> per question (u [K, D] float32, zq [D] float32, feats [K, 6] | None)"""
    rows, groups = [], []
    for qr in qrows:
        groups.append(list(range(len(rows), len(rows) + len(qr))))
        rows.extend(qr)
    if not rows:
        return []
    br = branch_forward(backbone, penc, rows, dev, open_ids, close_ids, lm_head=lm_head, prefix_pos=prefix_pos,
                        groups=groups, chunk=chunk, pad_id=pad_id)
    out = []
    for g in groups:
        gi = torch.tensor(g, device=dev)
        out.append((br["u"][gi].float(), br["zq"][g[0]].float(), (br["feats"][gi] if br["feats"] is not None else None)))
    return out
