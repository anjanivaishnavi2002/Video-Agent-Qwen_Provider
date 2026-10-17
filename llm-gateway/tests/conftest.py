import os
import sys
from pathlib import Path

os.environ.setdefault("GATEWAY_API_TOKEN", "test-token")
os.environ.setdefault("REQUIRE_TOKEN", "true")
for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
    os.environ[k] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
