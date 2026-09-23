"""Run the local Agent using OpenRouter's native chat adapter."""
import os
from pathlib import Path
import sys

from dotenv import load_dotenv


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        sys.exit("请先在项目根目录 .env 填写 OPENROUTER_API_KEY，再重新运行。")
    model = os.environ.get("OPENROUTER_MODEL", "nex-agi/nex-n2.5-mini:free")
    if not (model.endswith(":free") or model == "openrouter/free"):
        sys.exit("本地免费模式只允许 :free 模型或 openrouter/free。")
    # Existing component configuration selects the native OpenRouter adapter.
    os.environ.update(
        ANTHROPIC_API_KEY=key,
        ANTHROPIC_BASE_URL="https://openrouter.ai/api",
        ANTHROPIC_MODEL=model,
        OPENROUTER_MODEL=model,
        OPENROUTER_API_URL="https://openrouter.ai/api/v1/chat/completions",
    )
    os.chdir(root)
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "api.main:app",
                            "--host", "127.0.0.1", "--port", "8000",
                            "--timeout-graceful-shutdown", "5"])


if __name__ == "__main__":
    main()
