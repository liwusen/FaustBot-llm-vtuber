from __future__ import annotations

import os
from pathlib import Path

import torch


REPO_OR_DIR = "snakers4/silero-vad"
MODEL_NAME = "silero_vad"


def main() -> None:
    # 从环境变量读取 token（Actions 中自动存在）
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        torch.hub.set_auth_token(token)
    backend_root = Path(__file__).resolve().parents[1]
    torch_hub_dir = backend_root / "asr-hub" / "model" / "torch_hub"
    torch_hub_dir.mkdir(parents=True, exist_ok=True)

    print(f"[download_vad] torch hub dir: {torch_hub_dir}")
    torch.set_default_dtype(torch.float32)
    torch.hub.set_dir(str(torch_hub_dir))

    original_torch_home = os.environ.get("TORCH_HOME")
    os.environ["TORCH_HOME"] = str(torch_hub_dir)
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

    print("[download_vad] VAD model is ready.")


if __name__ == "__main__":
    main()