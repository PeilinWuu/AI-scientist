"""Direct test for CurlQwenProvider."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

from src.llm.curl_qwen_provider import CurlQwenProvider


def main() -> int:
    """Instantiate CurlQwenProvider and send one minimal prompt."""

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    try:
        provider = CurlQwenProvider()
        text = provider.generate(
            "You are a connectivity test assistant.",
            "Return exactly: curl provider ok",
        )
        print("CurlQwenProvider success: true")
        print(f"Response: {text}")
        print(f"Metadata: {provider.metadata()}")
        return 0
    except Exception as exc:  # noqa: BLE001 - explicit diagnostic script.
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        print("CurlQwenProvider success: false")
        print(_sanitize(traceback.format_exc(), api_key))
        return 1


def _sanitize(text: str, api_key: str) -> str:
    """Hide the configured API key in traceback output."""

    return text.replace(api_key, "[REDACTED_API_KEY]") if api_key else text


if __name__ == "__main__":
    raise SystemExit(main())
