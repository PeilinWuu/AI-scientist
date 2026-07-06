"""Unicode smoke test for CurlQwenProvider."""

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
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    provider = CurlQwenProvider()
    try:
        response = provider.generate(
            "你是一个中文连通性测试助手。",
            "请用中文返回一句：软体游动实验规划测试成功。",
        )
        print("CurlQwenProvider unicode success: true")
        print(f"Response preview: {response[:200]}")
        return 0
    except Exception:  # noqa: BLE001 - explicit diagnostic tool.
        api_key = os.getenv("DASHSCOPE_API_KEY", "")
        print("CurlQwenProvider unicode success: false")
        print(_sanitize(traceback.format_exc(), api_key))
        return 1


def _sanitize(text: str, api_key: str) -> str:
    return text.replace(api_key, "[REDACTED_API_KEY]") if api_key else text


if __name__ == "__main__":
    raise SystemExit(main())
