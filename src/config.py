"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=True)


class Settings:
    """Small settings object to avoid hard-coded secrets or paths."""

    def __init__(self) -> None:
        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY", "")
        self.llm_model = os.getenv("LLM_MODEL", "qwen-turbo")
        self.llm_search_model = os.getenv("LLM_SEARCH_MODEL", "qwen3.7-plus")
        self.llm_base_url = os.getenv(
            "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.responses_base_url = os.getenv("RESPONSES_BASE_URL", self.llm_base_url)
        self.llm_timeout = float(os.getenv("LLM_TIMEOUT", "60"))
        self.runs_dir = Path(os.getenv("RUNS_DIR", "runs"))


settings = Settings()
