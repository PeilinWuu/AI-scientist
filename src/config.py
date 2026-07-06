"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(override=True)


class Settings:
    """Small settings object to avoid hard-coded secrets or paths."""

    def __init__(self) -> None:
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")
        self.llm_model = os.getenv("LLM_MODEL", "qwen-turbo")
        self.llm_base_url = os.getenv(
            "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.llm_provider = os.getenv("LLM_PROVIDER", "mock").lower()
        self.qwen_transport = os.getenv("QWEN_TRANSPORT", "auto").lower()
        self.qwen_require_real = os.getenv("QWEN_REQUIRE_REAL", "false").lower() == "true"
        self.qwen_curl_timeout = int(os.getenv("QWEN_CURL_TIMEOUT", "60"))
        self.runs_dir = Path(os.getenv("RUNS_DIR", "runs"))


settings = Settings()
