from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

REPO_OR_DIR = "snakers4/silero-vad"
MODEL_NAME = "silero_vad"
CACHE_DIR_NAME = "snakers4_silero-vad_master"


def ensure_vad_cache(torch_hub_dir: Path, log_fn: Callable[[str], None] | None = None) -> None:
    """确保 silero-vad 已缓存到 ``torch_hub_dir``（无缓存时才联网）。

    被 CI 脚本 ``main()`` 与 ``VadProcessor.setup`` 共用，避免两份实现漂移。
    """
    import torch

    hub_dir = Path(torch_hub_dir)
    hub_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = hub_dir / CACHE_DIR_NAME
    if cache_dir.is_dir() and (cache_dir / "hubconf.py").is_file():
        if log_fn:
            log_fn(f"silero-vad 缓存命中: {cache_dir}")
        return

    if log_fn:
        log_fn(f"silero-vad 缓存缺失，联网下载到 {hub_dir}")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        torch.hub.set_auth_token(token)
    torch.hub.set_dir(str(hub_dir))
    original_torch_home = os.environ.get("TORCH_HOME")
    os.environ["TORCH_HOME"] = str(hub_dir)
    try:
        model, _utils = torch.hub.load(
            repo_or_dir=REPO_OR_DIR,
            model=MODEL_NAME,
            force_reload=False,
            trust_repo=True,
            onnx=False,
        )
        model.to("cpu")
        model.eval()
    finally:
        if original_torch_home is None:
            os.environ.pop("TORCH_HOME", None)
        else:
            os.environ["TORCH_HOME"] = original_torch_home
    if log_fn:
        log_fn("silero-vad 下载完成")


def main() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    torch_hub_dir = backend_root / "asr-hub" / "model" / "torch_hub"
    print(f"[download_vad] torch hub dir: {torch_hub_dir}")
    ensure_vad_cache(torch_hub_dir, log_fn=lambda msg: print(f"[download_vad] {msg}"))
    print("[download_vad] VAD model is ready.")


if __name__ == "__main__":
    main()
