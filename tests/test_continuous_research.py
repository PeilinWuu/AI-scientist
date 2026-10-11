"""Continuation/gate tests use in-memory steps; no network or model calls."""

import json

import pytest

from src.ai_scientist.continuous_research import HUMAN_GATES, TERMINAL, continue_research, pause_reason
from src.ai_scientist.job_store import ResearchJobStore
from src.ai_scientist.schemas import ResearchPhase, ResearchProject


class StepDouble:
    """Only invoke an explicit local transition supplied by the test."""

    def __init__(self, project, step=None):
        self.project = project
        self.step = step or (lambda project: None)
        self.calls = []

    def get_project(self, project_id):
        assert project_id == self.project.project_id
        return self.project

    def run_next_step(self, project_id, job_id=None):
        assert self.project.phase != ResearchPhase.EXECUTION, "Continuation silently executed code"
        self.calls.append((project_id, job_id, self.project.phase.value))
        self.step(self.project)
        return {"project_id": project_id, "current_phase": self.project.phase.value, "step_output": "recorded"}


def project_at(phase):
    return ResearchProject(title="Research continuation test", objective="Compare two methods", phase=phase)


@pytest.mark.parametrize("phase", sorted(HUMAN_GATES))
def test_each_existing_human_gate_stops_before_any_step(phase):
    project = project_at(ResearchPhase(phase))
    orchestrator = StepDouble(project)
    notifications = []
    result = continue_research(orchestrator, project.project_id, "job-test", on_stage=notifications.append)
    assert result == {"current_phase": phase, "pause_reason": "human_gate", "stages_run": 0}
    assert orchestrator.calls == []
    assert notifications == []


@pytest.mark.parametrize("phase", sorted(TERMINAL))
def test_each_terminal_state_is_not_restarted(phase):
    project = project_at(ResearchPhase(phase))
    orchestrator = StepDouble(project)
    result = continue_research(orchestrator, project.project_id, "job-test")
    assert result["pause_reason"] == "terminal"
    assert result["stages_run"] == 0
    assert orchestrator.calls == []


@pytest.mark.parametrize("phase", sorted(HUMAN_GATES | TERMINAL | {"EXECUTION"}))
def test_transition_into_gate_never_runs_the_following_step(phase):
    project = project_at(ResearchPhase.HYPOTHESIS_GENERATION)
    orchestrator = StepDouble(project, lambda p: setattr(p, "phase", ResearchPhase(phase)))
    result = continue_research(orchestrator, project.project_id, "job-test")
    assert result["current_phase"] == phase
    assert result["stages_run"] == 1
    assert len(orchestrator.calls) == 1
    assert result["step_output"] == "recorded"


def test_awaiting_code_approval_and_bound_executor_cannot_auto_execute():
    project = project_at(ResearchPhase.EXECUTION_WAITING)
    project.computation = {"status": "awaiting_approval", "plan_id": "plan-a"}
    orchestrator = StepDouble(project)
    assert continue_research(orchestrator, project.project_id, "job-test")["pause_reason"] == "code_approval_required"
    project.phase = ResearchPhase.EXECUTION
    project.execution_capability = "INTERNAL_EXECUTABLE"
    project.executor_binding = "test_executor"
    assert continue_research(orchestrator, project.project_id, "job-test")["pause_reason"] == "execution_confirmation_required"
    assert orchestrator.calls == []


def test_computation_preparation_can_continue_but_execution_stops_for_approval():
    project = project_at(ResearchPhase.EXECUTION_WAITING)

    def prepare(p):
        p.computation = {"status": "awaiting_approval", "plan_id": "new-plan"}

    orchestrator = StepDouble(project, prepare)
    result = continue_research(orchestrator, project.project_id, "job-test")
    assert result["pause_reason"] == "code_approval_required"
    assert result["stages_run"] == 1
    assert project.computation["plan_id"] == "new-plan"


@pytest.mark.parametrize("phase", [ResearchPhase.INTAKE, ResearchPhase.BACKGROUND_RESEARCH,
                                    ResearchPhase.EXECUTION_WAITING, ResearchPhase.SYNTHESIS])
def test_pending_model_question_blocks_all_further_steps(phase):
    project = project_at(phase)
    project.human_dialogue = {"pending": {"request_id": "ask-1", "prompt": "Which dataset should I use?"}}
    orchestrator = StepDouble(project)
    result = continue_research(orchestrator, project.project_id, "job-test")
    assert result["pause_reason"] == "model_requested_input"
    assert result["stages_run"] == 0
    assert orchestrator.calls == []


def test_model_question_raised_by_a_step_stops_same_phase_continuation():
    project = project_at(ResearchPhase.BACKGROUND_RESEARCH)

    def ask(p):
        p.human_dialogue = {"pending": {"request_id": "ask-1", "prompt": "Please supply data."}}

    orchestrator = StepDouble(project, ask)
    result = continue_research(orchestrator, project.project_id, "job-test")
    assert result["pause_reason"] == "model_requested_input"
    assert result["stages_run"] == 1
    assert len(orchestrator.calls) == 1


def test_answer_history_without_pending_does_not_itself_block():
    project = project_at(ResearchPhase.QUESTION_FORMULATION)
    project.human_dialogue = {"responses": [{"request_id": "answered", "response": "Use simulation."}]}
    assert pause_reason(project) is None


def test_no_progress_stops_without_spending_entire_batch_budget():
    project = project_at(ResearchPhase.DATA_ANALYSIS)
    orchestrator = StepDouble(project)
    result = continue_research(orchestrator, project.project_id, "job-test", max_steps=40)
    assert result["pause_reason"] == "no_progress"
    assert result["stages_run"] == 1
    assert len(orchestrator.calls) == 1


def test_background_six_action_batches_continue_until_a_real_human_gate():
    project = project_at(ResearchPhase.BACKGROUND_RESEARCH)
    project.background_agent = {"status": "ready", "observations": []}

    def batch(p):
        observations = p.background_agent["observations"]
        start = len(observations)
        observations.extend({"id": f"obs-{i}", "status": "completed"} for i in range(start, start + 6))
        if len(observations) == 18:
            p.phase = ResearchPhase.HUMAN_SOURCE_REVIEW

    orchestrator = StepDouble(project, batch)
    notifications = []
    result = continue_research(orchestrator, project.project_id, "job-test", on_stage=notifications.append)
    assert result["pause_reason"] == "human_gate"
    assert result["stages_run"] == 3
    assert len(project.background_agent["observations"]) == 18
    assert notifications == ["BACKGROUND_RESEARCH"] * 3
    assert all(call[1] == "job-test" for call in orchestrator.calls)


def test_continuation_is_bounded_even_if_every_step_records_progress():
    project = project_at(ResearchPhase.BACKGROUND_RESEARCH)
    project.background_agent = {"observations": []}
    orchestrator = StepDouble(project, lambda p: p.background_agent["observations"].append({"id": "an-observation"}))
    result = continue_research(orchestrator, project.project_id, "job-test", max_steps=3)
    assert result["pause_reason"] == "batch_limit"
    assert result["stages_run"] == 3
    assert len(orchestrator.calls) == 3


def test_user_pause_before_first_step_does_not_generate_or_execute():
    project = project_at(ResearchPhase.EXECUTION_WAITING)
    orchestrator = StepDouble(project)
    result = continue_research(orchestrator, project.project_id, "job-test", should_pause=lambda: True)
    assert result["pause_reason"] == "user_pause"
    assert result["stages_run"] == 0
    assert orchestrator.calls == []


def test_pause_after_current_background_batch_does_not_start_another():
    project = project_at(ResearchPhase.BACKGROUND_RESEARCH)
    project.background_agent = {"observations": []}
    orchestrator = StepDouble(project, lambda p: p.background_agent["observations"].extend([{}] * 6))
    result = continue_research(orchestrator, project.project_id, "job-test", should_pause=lambda: bool(orchestrator.calls))
    assert result["pause_reason"] == "user_pause"
    assert result["stages_run"] == 1
    assert len(orchestrator.calls) == 1


def test_step_failure_propagates_instead_of_being_reported_complete():
    project = project_at(ResearchPhase.STUDY_DESIGN)

    def fail(p):
        raise RuntimeError("model unavailable")

    orchestrator = StepDouble(project, fail)
    with pytest.raises(RuntimeError, match="model unavailable"):
        continue_research(orchestrator, project.project_id, "job-test")
    assert len(orchestrator.calls) == 1


def test_job_model_progress_updates_same_call_without_storing_private_fields(tmp_path):
    store = ResearchJobStore(tmp_path)
    job = store.create("project-test", "HYPOTHESIS_GENERATION")
    store.record_model_progress(job.project_id, job.job_id, {
        "call_id": "call-a", "agent_name": "hypothesis_scientist", "model": "test-model",
        "status": "streaming", "public_response": "I am checking the evidence.",
        "reasoning_content": "PRIVATE_REASONING", "prompt": "PRIVATE_PROMPT", "messages": ["PRIVATE_MESSAGES"],
        "raw_response": "PRIVATE_RAW", "internal_result": {"code": "PRIVATE_CODE"},
    })
    store.record_model_progress(job.project_id, job.job_id, {
        "call_id": "call-a", "status": "completed", "public_response": "Here is what the evidence supports.",
        "human_input_request": "Which outcome matters most?", "token_usage": {"total_tokens": 30},
    })
    updated = store.load(job.project_id, job.job_id)
    assert len(updated.model_updates) == 1
    assert updated.model_updates[0]["status"] == "completed"
    assert updated.model_updates[0]["public_response"] == "Here is what the evidence supports."
    assert updated.progress["phase"] == "HYPOTHESIS_GENERATION"
    assert "PRIVATE_" not in updated.model_dump_json()
    assert updated.progress["human_input_request"] == "Which outcome matters most?"


def test_pause_flag_and_model_updates_survive_each_other(tmp_path):
    store = ResearchJobStore(tmp_path)
    job = store.create("project-test", "BACKGROUND_RESEARCH")
    store.update(job.project_id, job.job_id, lambda j: setattr(j, "pause_requested", True))
    store.record_model_progress(job.project_id, job.job_id, {"call_id": "a", "public_response": "Source read."})
    store.update(job.project_id, job.job_id, lambda j: setattr(j, "phase", "CLAIM_EVIDENCE_MAPPING"))
    store.record_model_progress(job.project_id, job.job_id, {"call_id": "b", "public_response": "Assessing claims."})
    updated = store.load(job.project_id, job.job_id)
    assert updated.pause_requested
    assert len(updated.model_updates) == 2
    assert updated.model_updates[0]["phase"] == "BACKGROUND_RESEARCH"
    assert updated.model_updates[1]["phase"] == "CLAIM_EVIDENCE_MAPPING"


def test_progress_redacts_configured_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-private-credential")
    store = ResearchJobStore(tmp_path)
    job = store.create("project-test", "BACKGROUND_RESEARCH")
    store.record_model_progress(job.project_id, job.job_id, {
        "call_id": "a", "public_response": "Unexpected credential test-private-credential",
    })
    assert "test-private-credential" not in store.load(job.project_id, job.job_id).model_dump_json()


def test_progress_receipts_and_usage_cannot_smuggle_private_nested_fields(tmp_path):
    store = ResearchJobStore(tmp_path)
    job = store.create("project-test", "BACKGROUND_RESEARCH")
    store.record_model_progress(job.project_id, job.job_id, {
        "call_id": "a", "public_response": "Readable public explanation.",
        "receipt": {"request_id": "req-1", "response_model": "test-model", "reasoning_content": "PRIVATE_RECEIPT"},
        "provider_responses": [{"response_id": "resp-1", "messages": ["PRIVATE_PROVIDER_PROMPT"]}],
        "token_usage": {"total_tokens": 12, "raw_text": "PRIVATE_TOKEN_FIELD"},
    })
    updated = store.load(job.project_id, job.job_id)
    assert "PRIVATE_" not in json.dumps(updated.model_dump(mode="json"))
    assert updated.progress["receipt"]["request_id"] == "req-1"
    assert updated.progress["provider_responses"][0]["response_id"] == "resp-1"
    assert updated.progress["token_usage"] == {"total_tokens": 12}


class QuestionClient:
    """Return a valid role draft plus a public question on the first call only."""

    def __init__(self):
        self.calls = []

    def call(self, agent_name, instructions, payload, output_model):
        from src.ai_scientist.schemas import HypothesisOutput, ReproducibilityOutput, utc_now
        from src.ai_scientist.structured_client import StructuredCallMetadata, StructuredCallResult

        self.calls.append({"agent_name": agent_name, "payload": payload})
        if output_model == HypothesisOutput:
            output = HypothesisOutput(hypotheses=[], alternative_explanations=["This is a tentative draft."])
        elif output_model == ReproducibilityOutput:
            output = ReproducibilityOutput(execution_readiness="Missing dataset decision")
        else:
            pytest.fail(f"Another role was invoked before the researcher answered: {agent_name}")
        return StructuredCallResult(value=output, metadata=StructuredCallMetadata(
            agent_name=agent_name, requested_model="test-model", actual_model="test-model", fallback_used=False,
            started_at=utc_now(), finished_at=utc_now(), model_calls=1, attempted_calls=1, successful_calls=1,
            public_response="I need one choice from you before I can complete this stage.",
            human_input_request="Should this use a simulation or actual observations?" if len(self.calls) == 1 else "",
            call_id=f"call-{len(self.calls)}",
        ))


def test_model_question_keeps_original_stage_and_resumes_that_role_after_answer(tmp_path, monkeypatch):
    from src.ai_scientist.orchestrator import ResearchOrchestrator

    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Compare simulated methods")
    project.phase = ResearchPhase.HYPOTHESIS_GENERATION
    orchestrator.store.save(project)
    client = QuestionClient()
    monkeypatch.setattr(orchestrator, "_structured_client_for_project", lambda p: client)

    result = continue_research(orchestrator, project.project_id, "job-question")
    paused = orchestrator.get_project(project.project_id)
    assert result["pause_reason"] == "model_requested_input"
    assert paused.phase == ResearchPhase.HYPOTHESIS_GENERATION
    assert paused.human_dialogue["pending"]["request_id"] == "call-1"
    assert paused.active_job_id is None
    assert paused.budget.successful_model_calls == 1
    assert paused.budget.failed_model_calls == 0
    assert not any(a.artifact_type == "hypotheses" for a in paused.artifacts)
    assert any(a.artifact_type == "pending_model_draft" for a in paused.artifacts)
    assert not any(e.status == "failed" for e in orchestrator.list_events(project.project_id))

    # Mimic the persisted reply state without approving any existing human gate.
    answered = paused.human_dialogue.pop("pending")
    paused.human_dialogue.setdefault("responses", []).append({**answered, "response": "Use simulation only."})
    paused.revision_feedback.append("Use simulation only.")
    orchestrator.store.save(paused)
    resumed = continue_research(orchestrator, project.project_id, "job-resumed", max_steps=1)
    assert resumed["current_phase"] == "METHOD_SELECTION"
    assert [c["agent_name"] for c in client.calls] == ["hypothesis_scientist"] * 2
    feedback = client.calls[-1]["payload"]["shared_research_context"]["human_feedback"]
    assert feedback["human_dialogue"]["responses"][0]["response"] == "Use simulation only."
    assert any(a.artifact_type == "hypotheses" for a in orchestrator.get_project(project.project_id).artifacts)


def test_reproducibility_question_stops_before_independent_reviewer(tmp_path, monkeypatch):
    from src.ai_scientist.orchestrator import ResearchOrchestrator

    orchestrator = ResearchOrchestrator(tmp_path)
    project = orchestrator.create_project("Check reproducibility before review")
    project.phase = ResearchPhase.FEASIBILITY_REVIEW
    orchestrator.store.save(project)
    client = QuestionClient()
    monkeypatch.setattr(orchestrator, "_structured_client_for_project", lambda p: client)

    result = continue_research(orchestrator, project.project_id, "job-question")
    paused = orchestrator.get_project(project.project_id)
    assert result["pause_reason"] == "model_requested_input"
    assert [c["agent_name"] for c in client.calls] == ["reproducibility_engineer"]
    assert paused.phase == ResearchPhase.FEASIBILITY_REVIEW
    assert paused.reviews == []
    assert paused.reproducibility_plan == {}
    assert paused.budget.failed_model_calls == 0
