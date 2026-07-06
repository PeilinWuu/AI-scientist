"""Diagnose Qwen/DashScope connectivity without exposing API keys."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import certifi
import requests
import urllib3
from dotenv import load_dotenv
from openai import OpenAI
from requests import exceptions as request_exceptions


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
CHECK_PROMPT = "Return exactly: qwen check ok"


def main() -> int:
    """Run OpenAI SDK, requests, and curl.exe diagnostics in order."""

    load_dotenv(PROJECT_ROOT / ".env", override=True)

    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    model = os.getenv("LLM_MODEL", "qwen-turbo")
    base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    provider = os.getenv("LLM_PROVIDER", "mock")

    _print_environment_diagnostics(model, base_url, provider, api_key)

    if not api_key:
        print("No DASHSCOPE_API_KEY found. Mock mode is still available.")
        return 0

    sdk_result = _test_openai_sdk(api_key, base_url, model)
    requests_result = _test_requests(api_key, base_url, model)
    curl_result = _test_curl_fallback(api_key, base_url, model)

    if curl_result["success"] and not (sdk_result["success"] or requests_result["success"]):
        print(
            "Qwen API is reachable through curl.exe. "
            "Python HTTP stack may have SSL/proxy issues."
        )

    if sdk_result["success"]:
        print("Overall Qwen connectivity: success via OpenAI SDK")
        return 0
    if requests_result["success"]:
        print("Overall Qwen connectivity: success via requests")
        return 0
    if curl_result["success"]:
        print("Overall Qwen connectivity: success via curl.exe")
        return 0

    print("Overall Qwen connectivity: failed")
    return 1


def _print_environment_diagnostics(
    model: str, base_url: str, provider: str, api_key: str
) -> None:
    """Print safe diagnostics useful for SSL/proxy debugging."""

    print(f"Model: {model}")
    print(f"Base URL: {base_url}")
    print(f"Provider: {provider}")
    print(f"API Key: {_mask_key(api_key) if api_key else '<missing>'}")
    print(f"Python version: {platform.python_version()}")
    print(f"requests version: {requests.__version__}")
    print(f"urllib3 version: {urllib3.__version__}")
    print(f"certifi version: {_package_version('certifi')}")
    print(f"openai version: {_package_version('openai')}")
    print(f"HTTP_PROXY present: {'yes' if os.getenv('HTTP_PROXY') else 'no'}")
    print(f"HTTPS_PROXY present: {'yes' if os.getenv('HTTPS_PROXY') else 'no'}")


def _test_openai_sdk(api_key: str, base_url: str, model: str) -> dict[str, Any]:
    """Test Qwen through the official OpenAI SDK."""

    print("\n[1] OpenAI SDK test")
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": CHECK_PROMPT}],
            timeout=30,
        )
        text = response.choices[0].message.content or ""
        print("OpenAI SDK test success: true")
        print(f"Response preview: {text[:200]}")
        return {"success": True, "text": text}
    except Exception as exc:  # noqa: BLE001 - diagnostic tool should catch all.
        print("OpenAI SDK test success: false")
        print(f"OpenAI SDK error: {_sanitize_error(str(exc), api_key)}")
        return {"success": False, "error": str(exc)}


def _test_requests(api_key: str, base_url: str, model: str) -> dict[str, Any]:
    """Test Qwen with requests and print HTTP details."""

    print("\n[2] requests test")
    url = f"{base_url}/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": CHECK_PROMPT}],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "FlowScientist-Loop/0.1",
    }
    try:
        response = requests.post(url, json=body, headers=headers, timeout=30)
        print(f"HTTP status code: {response.status_code}")
        print(f"Response text preview: {response.text[:500]}")
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        print("requests test success: true")
        print(f"Response preview: {text[:200]}")
        return {"success": True, "text": text, "status_code": response.status_code}
    except (
        request_exceptions.SSLError,
        request_exceptions.ConnectionError,
        request_exceptions.Timeout,
        request_exceptions.HTTPError,
    ) as exc:
        print("requests test success: false")
        print(f"requests error: {_sanitize_error(str(exc), api_key)}")
        return {"success": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - catch malformed JSON or API shape issues.
        print("requests test success: false")
        print(f"requests unexpected error: {_sanitize_error(str(exc), api_key)}")
        return {"success": False, "error": str(exc)}


def _test_curl_fallback(api_key: str, base_url: str, model: str) -> dict[str, Any]:
    """Use curl.exe with a temporary JSON body as a Windows fallback."""

    print("\n[3] curl.exe fallback test")
    curl_path = _find_windows_curl()
    if not curl_path:
        print("curl.exe fallback success: false")
        print("curl.exe not found on PATH.")
        return {"success": False, "error": "curl.exe not found"}

    body = {
        "model": model,
        "messages": [{"role": "user", "content": CHECK_PROMPT}],
    }
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as temp_file:
            json.dump(body, temp_file, ensure_ascii=False)
            temp_path = temp_file.name

        command = [
            curl_path,
            "-i",
            "-X",
            "POST",
            f"{base_url}/chat/completions",
            "-H",
            f"Authorization: Bearer {api_key}",
            "-H",
            "Content-Type: application/json",
            "-H",
            "User-Agent: FlowScientist-Loop/0.1",
            "--data-binary",
            f"@{temp_path}",
        ]
        completed = _run_curl_command(command)
        stdout = _sanitize_error(completed.stdout, api_key)
        stderr = _sanitize_error(completed.stderr, api_key)
        success = _curl_succeeded(stdout)
        print(f"curl executable: {curl_path}")
        print(f"curl return code: {completed.returncode}")
        print(f"curl stdout preview: {stdout[:500]}")
        if stderr:
            print(f"curl stderr preview: {stderr[:500]}")

        if not success and (os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")):
            print("curl proxy-free retry: running without HTTP_PROXY/HTTPS_PROXY")
            no_proxy_completed = _run_curl_command(command, without_proxy=True)
            no_proxy_stdout = _sanitize_error(no_proxy_completed.stdout, api_key)
            no_proxy_stderr = _sanitize_error(no_proxy_completed.stderr, api_key)
            no_proxy_success = _curl_succeeded(no_proxy_stdout)
            print(f"curl proxy-free return code: {no_proxy_completed.returncode}")
            print(f"curl proxy-free stdout preview: {no_proxy_stdout[:500]}")
            if no_proxy_stderr:
                print(f"curl proxy-free stderr preview: {no_proxy_stderr[:500]}")
            success = no_proxy_success
            stdout = no_proxy_stdout
            stderr = no_proxy_stderr

        print(f"curl.exe fallback success: {'true' if success else 'false'}")
        return {"success": success, "stdout": stdout, "stderr": stderr}
    except Exception as exc:  # noqa: BLE001 - diagnostic tool should report all failures.
        print("curl.exe fallback success: false")
        print(f"curl error: {_sanitize_error(str(exc), api_key)}")
        return {"success": False, "error": str(exc)}
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


def _package_version(package_name: str) -> str:
    """Return a package version or a clear missing marker."""

    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return "<not installed>"


def _find_windows_curl() -> str | None:
    """Prefer Windows system curl.exe over Conda or other PATH variants."""

    system_curl = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
    if system_curl.exists():
        return str(system_curl)
    return shutil.which("curl.exe") or shutil.which("curl")


def _run_curl_command(
    command: list[str], without_proxy: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run curl with optional proxy environment removal."""

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
        timeout=45,
        check=False,
        env=env,
    )


def _curl_succeeded(stdout: str) -> bool:
    """Detect a successful Qwen response from curl output."""

    return "HTTP/1.1 200 OK" in stdout or "HTTP/2 200" in stdout or '"choices"' in stdout


def _mask_key(api_key: str) -> str:
    """Show only the first 8 and last 4 characters of the API key."""

    if len(api_key) <= 12:
        return "****"
    return f"{api_key[:8]}****{api_key[-4:]}"


def _sanitize_error(message: str, api_key: str) -> str:
    """Remove any accidental API key occurrence from output."""

    return message.replace(api_key, "[REDACTED_API_KEY]") if api_key else message


if __name__ == "__main__":
    raise SystemExit(main())
