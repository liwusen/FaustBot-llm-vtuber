"""Backbone-opinion features (v0.4), used by mso/infer.py: for every option block, the backbone's own
log-probability of the option text (sum, mean, length, its softmax over the K options, the first-token log-prob
at the question end, a validity flag) -> a 6-dim feature the decision head adds to the option's hidden state.
find_lm_head locates the language-model head behind a PEFT wrapper."""
import torch

N_FEAT = 6


def find_lm_head(backbone):
    for name in ("lm_head",):
        m = getattr(backbone, name, None)
        if m is not None:
            return m
    base = backbone.get_base_model() if hasattr(backbone, "get_base_model") else backbone
    return getattr(base, "lm_head")


def lm_option_feats(lm_head, h, ids, opens, closes, q_end, open_len, close_len):
    """h [T, D] (post-norm hidden states of one sequence), ids [T]; option k spans opens[k]..closes[k]
    inclusive of the markers. -> feats [K, N_FEAT] (float32) computed from the backbone's own
    next-token distribution: sum log p(option tokens), mean, n_tokens/10, softmax over K of the
    sums, first-token log p at the question end, and a validity flag."""
    K = min(len(opens), len(closes))
    dev = h.device
    feats = torch.zeros(K, N_FEAT, device=dev, dtype=torch.float32)
    if K == 0:
        return feats
    pred_pos, tgt_tok, owner = [], [], []
    firsts = []
    for k in range(K):
        o, c = opens[k], closes[k]
        inner = list(range(o + open_len, c - close_len + 1))       # token positions of the option text
        firsts.append(int(ids[inner[0]]) if inner else -1)
        for t in inner:
            pred_pos.append(t - 1)
            tgt_tok.append(int(ids[t]))
            owner.append(k)
    sums = torch.zeros(K, device=dev)
    cnts = torch.zeros(K, device=dev)
    if pred_pos:
        lg = lm_head(h[pred_pos]).float()
        lp = torch.log_softmax(lg, dim=-1)
        tok = torch.tensor(tgt_tok, device=dev)
        own = torch.tensor(owner, device=dev)
        picked = lp.gather(1, tok[:, None]).squeeze(1)
        sums = sums.index_add(0, own, picked)
        cnts = cnts.index_add(0, own, torch.ones_like(picked))
    valid = cnts > 0
    mean = torch.where(valid, sums / cnts.clamp(min=1), torch.zeros_like(sums))
    dist = torch.log_softmax(torch.where(valid, sums, torch.full_like(sums, -1e4)), dim=0)
    first = torch.zeros(K, device=dev)
    if q_end is not None and q_end >= 0 and any(f >= 0 for f in firsts):
        lq = torch.log_softmax(lm_head(h[q_end:q_end + 1]).float(), dim=-1)[0]
        for k, f in enumerate(firsts):
            if f >= 0:
                first[k] = lq[f]
    feats[:, 0] = sums / 10.0
    feats[:, 1] = mean
    feats[:, 2] = cnts / 10.0
    feats[:, 3] = dist
    feats[:, 4] = first
    feats[:, 5] = valid.float()
    return feats
