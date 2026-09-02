import os
import sys
from pathlib import Path

# Add 'src' to python module search path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from agentic_commerce.ui import launch_chat_app  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    launch_chat_app(port=port)
