"""Inference-side pieces used by mso/infer.py.

  OPT_OPEN / OPT_CLOSE   the two option-marker tokens (their embeddings ship with the checkpoint)
  render_option          how an option is written into the prompt (text, a region box, none of the above)
  video_content          one image, or a 4x4 frame mosaic split back into its timestamped frames
  q_items                the questions of a record, in either shape a question set may use
  Collator               tokenises a question with its option blocks and locates every option's span
  build_block_mask       options mutually invisible in attention, everything else causal
  add_option_tokens      registers the marker tokens with deterministic embeddings
  head_norm_of           the head normalisation a checkpoint was trained with
  MSO                    the model wrapper: backbone + decision head + ordinal head, mrope positions with
                         every option block sharing one start position (order invariance)
"""
import json
import os

import torch
import torch.nn as nn

from mso.head import OptionScorer, OrdinalScoreHead

OPT_OPEN, OPT_CLOSE = "<|opt|>", "<|/opt|>"


def render_option(o):
    if o.get("abstain"):
        return "none of the above"
    if "region" in o:
        b = [int(round(v)) for v in o["region"]["box"]]
        return "region [{},{},{},{}]".format(*b)
    return str(o.get("text", ""))[:200]


def video_content(img_path, video, instr):
    """One image, or a 4x4 mosaic split back into its frames, each preceded by its timestamp.
    Returns (list of PIL images in prompt order, chat content list)."""
    from PIL import Image
    im = Image.open(img_path).convert("RGB")
    if not video:
        return [im], [{"type": "image"}, {"type": "text", "text": instr}]
    K, cols, tile = video["n_frames"], video["cols"], video["tile"]
    frames = []
    content = [{"type": "text", "text": "A video of {:.0f} seconds; {} frames sampled uniformly in order.".format(
        video.get("duration", 0), K)}]
    for k in range(K):
        r, c = divmod(k, cols)
        frames.append(im.crop((c * tile, r * tile, (c + 1) * tile, (r + 1) * tile)))
        content.append({"type": "text", "text": "t={}s:".format(video["timestamps"][k])})
        content.append({"type": "image"})
    content.append({"type": "text", "text": instr})
    return frames, content


def q_items(rec):
    """-> list of questions, each carrying id/family, whatever shape the record uses"""
    qs = rec.get("questions") or []
    fam = rec.get("family", "?")
    if isinstance(qs, dict):
        out = []
        for qid, q in qs.items():
            d = dict(q)
            d.setdefault("id", qid)
            d.setdefault("family", q.get("family", fam))
            out.append(d)
        return out
    out = []
    for i, q in enumerate(qs):
        d = dict(q)
        d.setdefault("id", str(i))
        d.setdefault("family", q.get("family", fam))
        out.append(d)
    return out


class Collator:
    def __init__(self, proc, max_pixels):
        self.proc = proc
        self.tok = proc.tokenizer
        self.max_pixels = max_pixels

    def __call__(self, batch):
        from PIL import Image
        texts, images, spans, targets, types = [], [], [], [], []
        for b in batch:
            body = "".join("{}{}{}".format(OPT_OPEN, t, OPT_CLOSE) for t in b["opt_text"])
            frames, content = video_content(b["img"], b.get("video"), b["instr"])
            msg = [{"role": "user", "content": content}]
            prompt = self.proc.apply_chat_template(msg, tokenize=False, add_generation_prompt=True)
            texts.append(prompt + body)
            images.extend(frames)          # flat list in prompt order; the processor indexes it globally
            targets.append(b["tgt"])
            types.append(b["type"])
        enc = self.proc(text=texts, images=images, return_tensors="pt", padding=True)
        # locate each option's closing marker -> that position's hidden state is the option repr
        close_ids = self.tok(OPT_CLOSE, add_special_tokens=False)["input_ids"]
        open_ids = self.tok(OPT_OPEN, add_special_tokens=False)["input_ids"]
        ids = enc["input_ids"]
        for bi in range(ids.shape[0]):
            row = ids[bi].tolist()
            closes = [k for k in range(len(row) - len(close_ids) + 1)
                      if row[k:k + len(close_ids)] == close_ids]
            opens = [k for k in range(len(row) - len(open_ids) + 1)
                     if row[k:k + len(open_ids)] == open_ids]
            spans.append((opens, [c + len(close_ids) - 1 for c in closes]))
        return {"enc": enc, "spans": spans, "targets": targets, "types": types,
                "families": [b["family"] for b in batch]}


def build_block_mask(seq_len, spans, device, dtype):
    """Options mutually invisible; everything else causal. [1,1,L,L] additive."""
    ar = torch.arange(seq_len, device=device)
    m = (ar[:, None] >= ar[None, :])
    opens, closes = spans
    blocks = []
    for o, c in zip(opens, closes):
        if c >= o:
            blocks.append((o, c))
    for i, (o1, c1) in enumerate(blocks):
        for j, (o2, c2) in enumerate(blocks):
            if i == j:
                continue
            m[o1:c1 + 1, o2:c2 + 1] = False
    add = torch.zeros(seq_len, seq_len, device=device, dtype=dtype)
    add.masked_fill_(~m, torch.finfo(dtype).min)
    return add[None, None]


def tiny_backbone(model_path):
    """Random 2-layer Qwen3-VL for CPU validation of the whole train/eval path."""
    from transformers import AutoConfig, AutoModelForImageTextToText
    cfg = AutoConfig.from_pretrained(model_path)
    cfg.text_config.num_hidden_layers = 2
    cfg.text_config.hidden_size = 128
    cfg.text_config.intermediate_size = 256
    cfg.text_config.num_attention_heads = 4
    cfg.text_config.num_key_value_heads = 4
    cfg.text_config.head_dim = 32
    cfg.vision_config.depth = 2
    cfg.vision_config.hidden_size = 64
    cfg.vision_config.intermediate_size = 128
    cfg.vision_config.num_heads = 4
    cfg.vision_config.out_hidden_size = 128
    cfg.vision_config.deepstack_visual_indexes = [0, 1]
    torch.manual_seed(0)
    return AutoModelForImageTextToText.from_config(cfg).to(torch.float32)


def add_option_tokens(model, proc, emb_path=None):
    """Register <|opt|> <|/opt|> and give them DETERMINISTIC embeddings.

    resize_token_embeddings() fills new rows randomly. Those rows are frozen in
    training (only LoRA + head train), so a fresh random draw at eval time would
    silently change the very positions the head reads. We init them to the mean
    embedding and, when a saved copy exists, restore it exactly.
    """
    proc.tokenizer.add_special_tokens({"additional_special_tokens": [OPT_OPEN, OPT_CLOSE]})
    n_old = model.get_input_embeddings().weight.shape[0]
    model.resize_token_embeddings(len(proc.tokenizer))
    emb = model.get_input_embeddings().weight
    ids = proc.tokenizer.convert_tokens_to_ids([OPT_OPEN, OPT_CLOSE])
    with torch.no_grad():
        if emb_path and os.path.exists(emb_path):
            saved = torch.load(emb_path, map_location=emb.device)
            for tid, row in zip(ids, saved):
                emb[tid] = row.to(emb.dtype)
        else:
            mean = emb[:n_old].float().mean(0)
            for k, tid in enumerate(ids):
                emb[tid] = (mean + 0.01 * (k + 1)).to(emb.dtype)
    return ids


def _find_rope_owner(m, depth=0):
    """Qwen3VLModel owns get_rope_index; peft/DDP bury it a few attributes deep."""
    if depth > 6:
        return None
    if hasattr(m, "get_rope_index"):
        return m
    for attr in ("model", "base_model", "module"):
        sub = getattr(m, attr, None)
        if sub is not None and sub is not m:
            r = _find_rope_owner(sub, depth + 1)
            if r is not None:
                return r
    return None


def head_norm_of(ckpt_dir):
    """the head normalisation a checkpoint was trained with (ckpt/head_meta.json; v0.1/v0.2 = sigmoid)"""
    p = os.path.join(ckpt_dir, "head_meta.json")
    if os.path.exists(p):
        return json.load(open(p)).get("norm", "sigmoid")
    return "sigmoid"


class MSO(nn.Module):
    def __init__(self, backbone, d_model, n_types=3, head_norm="sigmoid"):
        super().__init__()
        self.backbone = backbone
        self.head = OptionScorer(d_model, d_hidden=min(1024, 2 * d_model), norm=head_norm)
        self.ord = OrdinalScoreHead(d_model, d_hidden=min(512, d_model))    # v0.4 ordinal Score head
        self._rope = _find_rope_owner(backbone)

    def rope_positions(self, enc, spans=None, align_options=True):
        """Compute mrope position_ids ourselves.

        Two reasons:
          1. Qwen3-VL derives them internally from a 2D attention_mask; once we pass
             a 4D block mask that path raises IndexError, so we must supply them.
          2. Having them in hand lets us give every option block the SAME starting
             position, which is what makes the answer exactly invariant to option
             order (tests/test_layout.py L1). Without this the blocks only differ
             by position, and order leaks back in through rope.
        """
        if self._rope is None:
            return None
        mm = enc.get("mm_token_type_ids")
        if mm is None:
            # transformers 5.x wants 0=text 1=image 2=video per token; rebuild it from ids
            cfg = getattr(self._rope, "config", None)
            img_id = getattr(cfg, "image_token_id", None)
            vid_id = getattr(cfg, "video_token_id", None)
            ids = enc["input_ids"]
            mm = torch.zeros_like(ids)
            if img_id is not None:
                mm[ids == img_id] = 1
            if vid_id is not None:
                mm[ids == vid_id] = 2
        kw = dict(input_ids=enc["input_ids"], image_grid_thw=enc.get("image_grid_thw"),
                  video_grid_thw=enc.get("video_grid_thw"), attention_mask=enc.get("attention_mask"))
        try:
            pos, _ = self._rope.get_rope_index(mm_token_type_ids=mm, **kw)      # transformers >= 5.x
        except TypeError:
            pos, _ = self._rope.get_rope_index(**kw)                            # older signature
        if align_options and spans is not None:
            pos = pos.clone()
            for bi, (opens, closes) in enumerate(spans):
                if not opens or not closes:
                    continue
                start = int(pos[0, bi, opens[0]].item())
                for o, c in zip(opens, closes):
                    if c < o:
                        continue
                    n = c - o + 1
                    rng = torch.arange(start, start + n, device=pos.device)
                    pos[:, bi, o:c + 1] = rng[None, :]
        return pos

    def hidden(self, enc, attn4d=None, spans=None, align_options=True):
        kw = {k: v for k, v in enc.items()}
        pos = self.rope_positions(enc, spans, align_options)
        if attn4d is not None:
            kw["attention_mask"] = attn4d
        if pos is not None:
            kw["position_ids"] = pos
        out = self.backbone(**kw, output_hidden_states=True, logits_to_keep=1)
        return out.hidden_states[-1]
