from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from pydantic import BaseModel

from src.ai_scientist.agents.base_agent import BaseResearchAgent
from src.ai_scientist.presentation import (
    determine_output_language,
    execution_capability_view,
    execution_result_view,
    feedback_signal_view,
    language_instruction,
    plan_adjustment_rows,
    status_label,
    user_error_message,
)
from src.ai_scientist.schemas import ResearchProject


class _LocalizedOutput(BaseModel):
    value: str


class _LocalizedAgent(BaseResearchAgent[_LocalizedOutput]):
    agent_name = "localized_test"
    output_model = _LocalizedOutput

    def build_payload(self, project: ResearchProject) -> dict:
        return {"objective": project.objective}


def test_known_statuses_are_chinese_and_unknown_status_is_preserved() -> None:
    assert status_label("EXECUTION_WAITING") == "等待实验执行"
    assert status_label("supported") == "有证据支持"
    assert status_label("future_status") == "future_status"


def test_execution_capability_explains_external_handoff() -> None:
    view = execution_capability_view("EXTERNAL_EXECUTION_REQUIRED")
    assert view["label"] == "需要研究者完成外部实验"
    assert "不会伪造" in view["description"]
    assert "CSV" in view["action"]


def test_language_preference_uses_chinese_and_allows_explicit_english() -> None:
    assert determine_output_language("研究阻尼振子的参数辨识") == "zh-CN"
    assert determine_output_language("研究阻尼振子。Please respond in English.") == "en"
    assert "简体中文" in language_instruction("zh-CN")


def test_agent_payload_includes_language_rule_without_changing_schema() -> None:
    captured: dict = {}

    class Client:
        def call(self, **kwargs):
            captured.update(kwargs["payload"])
            return SimpleNamespace(value=_LocalizedOutput(value="完成"), metadata=SimpleNamespace())

    class Skills:
        def load_for_agent(self, *_args):
            return []

        def compose_instructions(self, _skills):
            return "instructions"

    project = ResearchProject(title="中文任务", objective="研究一个可复现的科学问题")
    before_keys = set(project.model_dump(mode="json"))
    _LocalizedAgent(client=Client(), skill_loader=Skills()).run(project)

    assert captured["output_language"] == "zh-CN"
    assert "简体中文" in captured["language_rule"]
    assert set(project.model_dump(mode="json")) == before_keys


def test_execution_feedback_and_adjustment_presenters_do_not_mutate_data() -> None:
    result = {
        "status": "success",
        "seed": 20260831,
        "duration_ms": 21,
        "metrics": {"rmse": 0.03, "evaluations": 121, "best_damping": 0.17},
        "artifacts": [{"relative_path": "round_2/fit.png"}],
    }
    feedback = {
        "observed_result": {"rmse": 0.05},
        "quality_flags": ["rmse_above_success_threshold"],
        "source_artifact_ids": ["artifact_1"],
    }
    adjustment = {
        "old_value": {"damping": [0.05, 0.35], "points": [7, 9]},
        "new_value": {"damping": [0.15, 0.25], "points": [11, 11]},
        "reason": "refine",
        "evidence_refs": ["artifact_1"],
    }
    originals = deepcopy((result, feedback, adjustment))

    result_view = execution_result_view(result)
    feedback_view = feedback_signal_view(feedback)
    rows = plan_adjustment_rows(adjustment)

    assert result_view["status"] == "执行成功"
    assert result_view["rmse"] == 0.03
    assert result_view["artifacts"] == ["round_2/fit.png"]
    assert feedback_view["flags"] == ["RMSE 尚未达到成功阈值"]
    assert feedback_view["evidence_refs"] == ["artifact_1"]
    assert rows[0]["调整前"] == [0.05, 0.35]
    assert rows[0]["调整后"] == [0.15, 0.25]
    assert (result, feedback, adjustment) == originals


def test_error_message_hides_traceback_and_summarizes_validation_errors() -> None:
    assert "Traceback" not in user_error_message("Traceback: secret implementation detail")
    message = user_error_message([{"msg": "Extra inputs are not permitted"}])
    assert message == "提交内容有误：Extra inputs are not permitted"


def test_streamlit_keeps_raw_data_collapsed_for_developers() -> None:
    source = open("app_streamlit.py", encoding="utf-8").read()
    assert "查看原始结构化数据（开发者）" in source
    assert "st.json(execution_summary)" in source
    assert "Start Research" not in source
    assert "Advanced Settings" not in source
