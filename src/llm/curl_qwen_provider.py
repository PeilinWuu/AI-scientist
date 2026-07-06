"""Qwen provider that calls Alibaba Cloud Bailian through curl."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.config import settings
from src.llm.base import LLMProvider


class CurlQwenProvider(LLMProvider):
    """Real Qwen provider for Windows/Python SSL stack fallback cases."""

    def __init__(self) -> None:
        self.api_key = os.getenv("DASHSCOPE_API_KEY", settings.dashscope_api_key)
        if not self.api_key:
            raise ValueError("DASHSCOPE_API_KEY is required when using CurlQwenProvider.")
        self.model = os.getenv("LLM_MODEL", settings.llm_model or "qwen-turbo")
        self.base_url = os.getenv(
            "LLM_BASE_URL",
            settings.llm_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).rstrip("/")
        self.timeout = int(os.getenv("QWEN_CURL_TIMEOUT", str(settings.qwen_curl_timeout)))
        self.curl_candidates = self._find_curl_candidates()
        self.curl_path = self.curl_candidates[0] if self.curl_candidates else ""
        if not self.curl_candidates:
            raise RuntimeError("curl.exe or curl was not found on PATH.")

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Call Qwen through curl and return choices[0].message.content."""

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as temp_file:
                json.dump(body, temp_file, ensure_ascii=False)
                temp_path = temp_file.name

            stdout, stderr = self._try_curl_candidates(temp_path)
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Curl Qwen returned non-JSON output: {stdout[:500]}") from exc
            if "error" in data:
                error = data["error"] or {}
                message = error.get("message", "Unknown Qwen API error")
                code = error.get("code", "unknown_code")
                raise RuntimeError(f"Qwen API error {code}: {message}")
            content = data["choices"][0]["message"].get("content")
            if content is None:
                raise RuntimeError(f"Qwen response has no message content: {data}")
            return str(content)
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)

    def metadata(self) -> dict:
        return {
            "provider": "qwen",
            "transport": "curl",
            "model": self.model,
            "base_url": self.base_url,
            "is_mock": False,
        }

    def _build_command(self, temp_path: str) -> list[str]:
        return [
            self.curl_path,
            "-sS",
            "-X",
            "POST",
            f"{self.base_url}/chat/completions",
            "-H",
            f"Authorization: Bearer {self.api_key}",
            "-H",
            "Content-Type: application/json",
            "-H",
            "User-Agent: FlowScientist-Loop/0.1",
            "--data-binary",
            f"@{temp_path}",
        ]

    def _try_curl_candidates(self, temp_path: str) -> tuple[str, str]:
        """Try PATH curl variants and proxy-free retry before failing."""

        errors: list[str] = []
        for curl_path in self.curl_candidates:
            self.curl_path = curl_path
            command = self._build_command(temp_path)
            for without_proxy in [False, True] if self._has_proxy_env() else [False]:
                completed = self._run(command, without_proxy=without_proxy)
                stdout = self._sanitize(completed.stdout)
                stderr = self._sanitize(completed.stderr)
                if completed.returncode == 0:
                    return stdout, stderr
                mode = "proxy-free" if without_proxy else "with-proxy"
                errors.append(
                    f"{curl_path} ({mode}) code={completed.returncode} stderr={stderr[:300]}"
                )
        raise RuntimeError("Curl Qwen request failed. " + " | ".join(errors))

    def _run(
        self, command: list[str], without_proxy: bool = False
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if without_proxy:
            for key in [
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            ]:
                env.pop(key, None)
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
            shell=False,
            env=env,
        )

    def _sanitize(self, text: str | None) -> str:
        """Sanitize subprocess output while tolerating missing stdout/stderr."""

        if text is None:
            return ""
        api_key = self.api_key or ""
        return text.replace(api_key, "[REDACTED_API_KEY]") if api_key else text

    def _has_proxy_env(self) -> bool:
        return bool(os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY"))

    def masked_api_key(self) -> str:
        if len(self.api_key) <= 12:
            return "****"
        return f"{self.api_key[:8]}****{self.api_key[-4:]}"

    def _find_curl_candidates(self) -> list[str]:
        """Return curl candidates, including Windows system curl if available."""

        candidates: list[str] = []
        for path in [shutil.which("curl.exe"), shutil.which("curl")]:
            if path and path not in candidates:
                candidates.append(path)
        system_curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
        if system_curl.exists() and str(system_curl) not in candidates:
            candidates.append(str(system_curl))
        return candidates
