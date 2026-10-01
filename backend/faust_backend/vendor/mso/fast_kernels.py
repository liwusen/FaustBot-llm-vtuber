"""Route Qwen3.5's gated delta rule through the flash-linear-attention Triton kernels.

transformers decorates its pure-torch `torch_chunk_gated_delta_rule` / `torch_recurrent_gated_delta_rule`
with a resolver that looks for the kernels at fla's top level; the installed fla exports them from
`fla.ops`, so the resolver silently keeps the torch fallback (a Python loop over chunks / tokens, fp32).
`enable_fla()` swaps the two module-level functions for thin wrappers around `fla.ops` (same
argument names and [batch, seq, heads, dim] layout). MSO_FLA=0 disables; returns True when active.
fp32 outputs agree with the torch path to 1e-4."""
import os


def enable_fla(verbose=True):
    if os.environ.get("MSO_FLA", "1") == "0":
        return False
    try:
        from fla.ops import chunk_gated_delta_rule, fused_recurrent_gated_delta_rule
        import transformers.models.qwen3_5.modeling_qwen3_5 as M
    except Exception as e:                      # fla or the model module missing: keep the torch path
        if verbose:
            print("fast_kernels: fla not used (%s)" % str(e)[:80], flush=True)
        return False
    if getattr(M, "_mso_fla", False):
        return True

    def _prep(query, key, value, g, beta):
        return query, key, value, g.float(), beta.to(query.dtype)

    def chunk(query, key, value, g, beta, chunk_size=64, initial_state=None, output_final_state=False,
              use_qk_l2norm_in_kernel=False, **kw):
        q, k, v, g, b = _prep(query, key, value, g, beta)
        o, st = chunk_gated_delta_rule(q, k, v, g, b, initial_state=initial_state, output_final_state=output_final_state,
                                       use_qk_l2norm_in_kernel=use_qk_l2norm_in_kernel, cu_seqlens=kw.get("cu_seqlens"))
        return o, (st if output_final_state else None)

    def recurrent(query, key, value, g, beta, initial_state=None, output_final_state=False,
                  use_qk_l2norm_in_kernel=False, **kw):
        q, k, v, g, b = _prep(query, key, value, g, beta)
        o, st = fused_recurrent_gated_delta_rule(q, k, v, g, b, initial_state=initial_state, output_final_state=output_final_state,
                                                 use_qk_l2norm_in_kernel=use_qk_l2norm_in_kernel, cu_seqlens=kw.get("cu_seqlens"))
        return o, (st if output_final_state else None)

    M.torch_chunk_gated_delta_rule = chunk
    M.torch_recurrent_gated_delta_rule = recurrent
    M._mso_fla = True
    if verbose:
        print("fast_kernels: fla Triton kernels active for Qwen3.5 linear attention", flush=True)
    return True
