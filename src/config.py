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
        self.llm_base_url = os.getenv(
            "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.llm_provider = os.getenv("LLM_PROVIDER", "mock").lower()
        self.qwen_transport = os.getenv("QWEN_TRANSPORT", "auto").lower()
        self.qwen_require_real = os.getenv("QWEN_REQUIRE_REAL", "false").lower() == "true"
        self.qwen_curl_timeout = int(os.getenv("QWEN_CURL_TIMEOUT", "60"))
        self.qwen_enable_search = os.getenv("QWEN_ENABLE_SEARCH", "false").lower() == "true"
        self.qwen_search_strategy = os.getenv("QWEN_SEARCH_STRATEGY", "turbo")
        self.qwen_search_force = os.getenv("QWEN_SEARCH_FORCE", "false").lower() == "true"
        freshness = os.getenv("QWEN_SEARCH_FRESHNESS", "").strip()
        self.qwen_search_freshness = int(freshness) if freshness.isdigit() else None
        assigned_sites = os.getenv("QWEN_SEARCH_ASSIGNED_SITES", "").strip()
        self.qwen_search_assigned_sites = [
            item.strip() for item in assigned_sites.split(",") if item.strip()
        ]
        self.runs_dir = Path(os.getenv("RUNS_DIR", "runs"))


settings = Settings()
