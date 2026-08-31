import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import src.ai_scientist.competition_readiness as readiness


def _write_evidence(root: Path, **updates: object) -> Path:
    payload = {
        "provider": "Alibaba Cloud Bailian / Qwen",
        "model": "qwen3.8-max",
        "base_url": "https://workspace.example/compatible-mode/v1",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASSED",
        "response_marker": "QWEN_SMOKE_OK",
        "success": True,
        "error_type": None,
        "error_message": None,
        "latency_ms": 123,
    }
    payload.update(updates)
    path = root / "results/qwen_smoke_evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_qwen_status_without_evidence_is_not_run(tmp_path: Path) -> None:
    result = readiness._qwen_smoke_status(tmp_path)
    assert result == {
        "status": "NOT_RUN",
        "passed": False,
        "evidence": "results/qwen_smoke_evidence.json",
    }


def test_qwen_status_reads_fresh_success_evidence(tmp_path: Path) -> None:
    _write_evidence(tmp_path)
    result = readiness._qwen_smoke_status(tmp_path)
    assert result["status"] == "PASSED"
    assert result["passed"] is True


def test_qwen_status_reads_failure_and_rejects_expired_evidence(tmp_path: Path) -> None:
    _write_evidence(tmp_path, status="FAILED", success=False, error_type="AuthenticationError")
    failed = readiness._qwen_smoke_status(tmp_path)
    assert failed["status"] == "FAILED"
    assert failed["passed"] is False

    _write_evidence(
        tmp_path,
        verified_at=(datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
    )
    expired = readiness._qwen_smoke_status(tmp_path)
    assert expired["status"] == "EXPIRED"
    assert expired["passed"] is False


def test_real_smoke_uses_client_and_saves_no_secret(tmp_path: Path, monkeypatch) -> None:
    secret = "competition-secret-value"
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)
    monkeypatch.setenv("LLM_MODEL", "qwen3.8-max")
    monkeypatch.setenv("LLM_BASE_URL", f"https://user:{secret}@workspace.example/compatible-mode/v1?api_key={secret}")

    class SuccessfulClient:
        model = "qwen3.8-max"
        base_url = f"https://user:{secret}@workspace.example/compatible-mode/v1?api_key={secret}"

        def chat(self, messages: list[dict[str, str]]) -> str:
            assert messages == [{"role": "user", "content": "Return exactly: QWEN_SMOKE_OK"}]
            return "QWEN_SMOKE_OK"

    evidence = readiness.run_qwen_smoke(tmp_path, client_factory=SuccessfulClient)
    persisted = (tmp_path / "results/qwen_smoke_evidence.json").read_text(encoding="utf-8")
    assert evidence["success"] is True
    assert evidence["status"] == "PASSED"
    assert evidence["model"] == "qwen3.8-max"
    assert evidence["base_url"] == "https://workspace.example/compatible-mode/v1"
    assert secret not in persisted
    assert "Authorization" not in persisted


def test_failed_smoke_redacts_secret_and_authorization(tmp_path: Path, monkeypatch) -> None:
    secret = "competition-secret-value"
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)

    class FailedClient:
        def __init__(self) -> None:
            raise RuntimeError(f"Authorization: Bearer {secret}; api_key={secret}")

    evidence = readiness.run_qwen_smoke(tmp_path, client_factory=FailedClient)
    serialized = json.dumps(evidence)
    assert evidence["success"] is False
    assert evidence["error_type"] == "RuntimeError"
    assert secret not in serialized


def test_normal_readiness_reads_evidence_without_external_call(tmp_path: Path, monkeypatch) -> None:
    _write_evidence(tmp_path)
    (tmp_path / "results/test_summary.json").write_text(
        json.dumps({"passed": True, "exit_code": 0, "output": "tests passed"}), encoding="utf-8"
    )

    def forbidden_call(*args, **kwargs):
        raise AssertionError("ordinary readiness must not call Qwen")

    monkeypatch.setattr(readiness, "run_qwen_smoke", forbidden_call)
    result = readiness.check_readiness(tmp_path, run_tests=False)
    assert result["qwen_real_smoke_test_status"] == "PASSED"
    assert result["qwen_real_smoke_test_passed"] is True


def test_run_qwen_smoke_cli_path_is_mockable(monkeypatch, capsys) -> None:
    calls: list[str] = []

    def fake_smoke(root: str) -> dict:
        calls.append(root)
        return {"status": "PASSED", "success": True}

    monkeypatch.setattr(readiness, "run_qwen_smoke", fake_smoke)
    monkeypatch.setattr(readiness, "check_readiness", lambda root, run_tests: {"ready": True})
    monkeypatch.setattr(sys, "argv", ["competition_readiness", "--root", "demo", "--run-qwen-smoke", "--skip-tests"])
    assert readiness.main() == 0
    assert calls == ["demo"]
    assert "qwen_smoke_evidence" in capsys.readouterr().out
