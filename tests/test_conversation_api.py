"""API conversation and continuation regressions; all model work is local doubles."""

from types import SimpleNamespace
import hashlib
import json
import base64

import pytest
from fastapi.testclient import TestClient

from src import main_api
from src.ai_scientist.exceptions import ModelInputRequired
from src.ai_scientist.job_store import ResearchJobStore
from src.ai_scientist.model_progress import emit_model_progress
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ApprovedRevisionPlan, ResearchPhase, RevisionIssue, RevisionTargetBatch


@pytest.fixture
def api(tmp_path, monkeypatch):
    orchestrator = ResearchOrchestrator(tmp_path)
    jobs = ResearchJobStore(tmp_path)
    monkeypatch.setattr(main_api, "research_orchestrator", orchestrator)
    monkeypatch.setattr(main_api, "research_job_store", jobs)
    monkeypatch.setattr(main_api, "project_request_locks", {})
    threads = []

    class DeferredThread:
        def __init__(self, *, target, args, daemon, name=None):
            self.target, self.args = target, args
            threads.append(self)

        def start(self):
            pass

        def run(self):
            self.target(*self.args)

    monkeypatch.setattr(main_api.threading, "Thread", DeferredThread)
    project = orchestrator.create_project("比较两种方法，请先说明需要的数据。", research_policy_version=2)
    project.phase = ResearchPhase.HYPOTHESIS_GENERATION
    orchestrator.store.save(project)
    return SimpleNamespace(client=TestClient(main_api.app), o=orchestrator, jobs=jobs, project=project,
                           threads=threads, monkeypatch=monkeypatch)


@pytest.mark.parametrize('change', ['none', 'modified', 'symlink', 'unknown'])
def test_computation_download_verifies_exact_manifest(api, change):
    p = api.project
    root = api.o.store.project_dir(p.project_id)
    relative = 'computation/inline_run/output/figure.png'
    target = root / relative
    target.parent.mkdir(parents=True)
    content = b'original output bytes'
    target.write_bytes(content)
    p.computation = {'attempts': [{'run_id': 'inline_run', 'status': 'success', 'artifacts': [
        {'relative_path': relative, 'checksum_sha256': hashlib.sha256(content).hexdigest(),
         'size_bytes': len(content)}]}]}
    api.o.store.save(p)
    if change == 'modified':
        target.write_bytes(b'changed bytes')
    elif change == 'symlink':
        target.unlink()
        other = root / 'other.png'
        other.write_bytes(content)
        target.symlink_to(other)
    elif change == 'unknown':
        relative = 'computation/unlisted/output/figure.png'
    response = api.client.get(f'/api/research/{p.project_id}/computation/file', params={'path': relative})
    assert response.status_code == {'none': 200, 'modified': 409, 'symlink': 409, 'unknown': 404}[change]
    if change == 'none':
        assert base64.b64decode(response.json()['content_base64']) == content


def test_manual_title_has_persistent_ownership(api):
    response = api.client.patch(f'/api/research/{api.project.project_id}/title', json={'title': '用户研究名称'})
    assert response.status_code == 200
    saved = api.o.get_project(api.project.project_id)
    assert saved.title == '用户研究名称'
    assert saved.title_user_edited is True


def test_background_question_reply_resumes_without_approval(api):
    p = api.project
    p.phase = ResearchPhase.HUMAN_SOURCE_REVIEW
    p.human_dialogue['pending'] = {'request_id': 'request-test', 'prompt': '请确认范围',
                                 'resume_phase': 'BACKGROUND_RESEARCH'}
    api.o.store.save(p)
    response = api.client.post(f'/api/research/{p.project_id}/reply',
                               json={'request_id': 'request-test', 'response': '限定范围'})
    assert response.status_code == 200
    saved = api.o.get_project(p.project_id)
    assert saved.phase == ResearchPhase.BACKGROUND_RESEARCH
    assert saved.approval_status != 'valid'
    assert not saved.human_dialogue.get('pending')


def test_continue_worker_persists_live_public_output_and_conversation(api):
    seen_streaming = []

    def step(project_id, job_id=None):
        emit_model_progress({"call_id": "call-1", "agent_name": "hypothesis_scientist", "model": "fake-qwen",
                             "timestamp": "2026-10-10T12:00:00Z", "status": "started"})
        emit_model_progress({"call_id": "call-1", "agent_name": "hypothesis_scientist", "model": "fake-qwen",
                             "timestamp": "2026-10-10T12:00:01Z", "status": "streaming",
                             "public_response": "我正在检查两种方法的区别。", "reasoning_content": "PRIVATE_STREAM"})
        interim = api.jobs.load(project_id, job_id)
        seen_streaming.append(interim.model_updates[0]["public_response"])
        assert interim.status == "running"
        emit_model_progress({"call_id": "call-1", "agent_name": "hypothesis_scientist", "model": "fake-qwen",
                             "timestamp": "2026-10-10T12:00:02Z", "status": "completed",
                             "public_response": "两种方法需要相同输入，请确认方案。",
                             "receipt": {"request_id": "fake-request", "response_model": "fake-qwen"}})
        project = api.o.get_project(project_id)
        project.phase = ResearchPhase.HUMAN_APPROVAL
        api.o.store.save(project)
        return {"current_phase": project.phase.value}

    api.monkeypatch.setattr(api.o, "run_next_step", step)
    queued = api.client.post(f"/api/research/{api.project.project_id}/continue_async")
    assert queued.status_code == 202
    assert queued.json()["run_mode"] == "until_human_gate"
    assert len(api.threads) == 1
    api.threads[0].run()
    job_id = queued.json()["job_id"]
    saved = api.client.get(f"/api/research/jobs/{job_id}").json()
    assert seen_streaming == ["我正在检查两种方法的区别。"]
    assert saved["status"] == "completed"
    assert saved["result"]["pause_reason"] == "human_gate"
    assert saved["result"]["stages_run"] == 1
    response = api.client.get(f"/api/research/{api.project.project_id}/conversation")
    assert response.status_code == 200
    message, = response.json()["messages"]
    assert message["public_response"] == "两种方法需要相同输入，请确认方案。"
    assert message["status"] == "completed"
    assert message["job_id"] == job_id
    assert message["receipt"]["request_id"] == "fake-request"
    assert "PRIVATE_STREAM" not in response.text


def seed_pending(api, phase=ResearchPhase.HYPOTHESIS_GENERATION):
    p = api.o.get_project(api.project.project_id)
    p.phase = phase
    p.human_dialogue = {"pending": {"request_id": "ask-current", "prompt": "使用模拟还是实际数据？",
                                    "phase": phase.value, "agent_name": "hypothesis_scientist"}}
    api.o.store.save(p)
    return p


@pytest.mark.parametrize("response", ["", " ", "\n\t"])
def test_empty_reply_does_not_consume_pending_request(api, response):
    p = seed_pending(api)
    before = api.o.get_project(p.project_id).human_dialogue
    result = api.client.post(f"/api/research/{p.project_id}/reply",
                             json={"request_id": "ask-current", "response": response})
    assert result.status_code == 422
    assert api.o.get_project(p.project_id).human_dialogue == before


def test_stale_reply_does_not_change_pending_or_feedback(api):
    p = seed_pending(api)
    result = api.client.post(f"/api/research/{p.project_id}/reply",
                             json={"request_id": "old-request", "response": "使用模拟。"})
    assert result.status_code == 409
    saved = api.o.get_project(p.project_id)
    assert saved.human_dialogue == p.human_dialogue
    assert saved.revision_feedback == p.revision_feedback


@pytest.mark.parametrize("job_status", ["queued", "running"])
def test_reply_is_blocked_while_project_worker_is_active(api, job_status):
    p = seed_pending(api)
    job = api.jobs.create(p.project_id, p.phase.value)
    job.status = job_status
    api.jobs.save(job)
    result = api.client.post(f"/api/research/{p.project_id}/reply",
                             json={"request_id": "ask-current", "response": "使用模拟。"})
    assert result.status_code == 409
    assert result.json()["detail"]["job_id"] == job.job_id
    assert api.o.get_project(p.project_id).human_dialogue == p.human_dialogue


@pytest.mark.parametrize("phase", [ResearchPhase.HYPOTHESIS_GENERATION, ResearchPhase.HUMAN_APPROVAL])
def test_reply_is_saved_once_and_never_implies_approval_or_phase_change(api, phase):
    p = seed_pending(api, phase)
    request = {"request_id": "ask-current", "response": "使用模拟，不要当成真实观测。"}
    result = api.client.post(f"/api/research/{p.project_id}/reply", json=request)
    assert result.status_code == 200
    assert result.json()["status"] == "received"
    saved = api.o.get_project(p.project_id)
    assert "pending" not in saved.human_dialogue
    answer, = saved.human_dialogue["responses"]
    assert answer["response"] == request["response"]
    assert answer["prompt"] == p.human_dialogue["pending"]["prompt"]
    assert answer["answered_at"]
    assert saved.revision_feedback == p.revision_feedback
    assert saved.phase == phase
    assert saved.approval_status == p.approval_status
    assert saved.human_approval_history == []
    duplicate = api.client.post(f"/api/research/{p.project_id}/reply", json=request)
    assert duplicate.status_code == 409
    assert len(api.o.get_project(p.project_id).human_dialogue["responses"]) == 1


@pytest.mark.parametrize("operation", ["prepare", "execute", "resume_assessment"])
def test_computation_model_question_completes_job_without_claiming_failure(api, operation):
    p = api.o.get_project(api.project.project_id)
    p.phase = ResearchPhase.EXECUTION_WAITING
    api.o.store.save(p)
    job = api.jobs.create(p.project_id, p.phase.value)

    def ask(*args, **kwargs):
        project = api.o.get_project(p.project_id)
        project.human_dialogue = {"pending": {"request_id": "ask-computation", "prompt": "需要哪一个误差指标？"}}
        api.o.store.save(project)
        emit_model_progress({"call_id": "ask-computation", "status": "completed",
                             "public_response": "需要先确认误差指标。", "human_input_request": "需要哪一个误差指标？"})
        raise ModelInputRequired("需要哪一个误差指标？")

    api.monkeypatch.setattr(main_api, "computation_service", SimpleNamespace(
        prepare=ask, execute=ask, resume_assessment=ask))
    main_api._run_computation_job(p.project_id, job.job_id, operation, "plan-test")
    response = api.client.get(f"/api/research/jobs/{job.job_id}")
    assert response.status_code == 200
    saved = response.json()
    assert saved["status"] == "completed"
    assert saved["error"] is None
    assert saved["result"]["status"] == "awaiting_model_requested_input"
    assert saved["finished_at"]
    assert saved["model_updates"][0]["human_input_request"] == "需要哪一个误差指标？"
    assert api.o.get_project(p.project_id).human_dialogue["pending"]["request_id"] == "ask-computation"


def test_pause_before_continuation_keeps_public_records_and_runs_no_stage(api):
    queued = api.client.post(f"/api/research/{api.project.project_id}/continue_async")
    assert queued.status_code == 202
    job_id = queued.json()["job_id"]
    api.jobs.record_model_progress(api.project.project_id, job_id, {
        "call_id": "earlier-message", "public_response": "此前的研究进展应保留。", "status": "completed"})
    pause = api.client.post(f"/api/research/jobs/{job_id}/pause")
    assert pause.status_code == 200
    assert pause.json()["pause_requested"] is True
    api.monkeypatch.setattr(api.o, "run_next_step", lambda *args, **kwargs: pytest.fail("A paused job started work"))
    api.threads[0].run()
    saved = api.jobs.load(api.project.project_id, job_id)
    assert saved.status == "completed"
    assert saved.pause_requested
    assert saved.result["pause_reason"] == "user_pause"
    assert saved.result["stages_run"] == 0
    messages = api.client.get(f"/api/research/{api.project.project_id}/conversation").json()["messages"]
    assert messages[0]["public_response"] == "此前的研究进展应保留。"
    assert api.client.post(f"/api/research/jobs/{job_id}/pause").status_code == 409


def test_duplicate_continuation_is_rejected_without_creating_second_job(api):
    url = f"/api/research/{api.project.project_id}/continue_async"
    first = api.client.post(url)
    second = api.client.post(url)
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["detail"]["job_id"] == first.json()["job_id"]
    assert len(api.threads) == 1


def test_conversation_orders_calls_across_jobs_and_omits_empty_progress(api):
    later = api.jobs.create(api.project.project_id, "STUDY_DESIGN")
    earlier = api.jobs.create(api.project.project_id, "HYPOTHESIS_GENERATION")
    api.jobs.record_model_progress(api.project.project_id, later.job_id, {
        "call_id": "later", "timestamp": "2026-10-10T12:02:00Z", "public_response": "Next stage."})
    api.jobs.record_model_progress(api.project.project_id, earlier.job_id, {
        "call_id": "empty", "timestamp": "2026-10-10T12:00:00Z", "status": "started"})
    api.jobs.record_model_progress(api.project.project_id, earlier.job_id, {
        "call_id": "earlier", "timestamp": "2026-10-10T12:01:00Z", "public_response": "Earlier stage."})
    api.jobs.record_model_progress(api.project.project_id, later.job_id, {
        "call_id": "question", "timestamp": "2026-10-10T12:03:00Z", "human_input_request": "Please select a dataset."})
    response = api.client.get(f"/api/research/{api.project.project_id}/conversation")
    assert response.status_code == 200
    assert [message["call_id"] for message in response.json()["messages"]] == ["earlier", "later", "question"]


@pytest.mark.parametrize("target", ["execution_code", "result_table", "figure", "reproducibility_bundle"])
def test_execution_revision_dispatch_requires_new_code_approval(api, target):
    p = api.o.get_project(api.project.project_id)
    issue = RevisionIssue(target=target, classification="plan_blocking", problem="实际交付缺失",
                          completion_criteria=["交付完整真实产物"], status="approved")
    batch = RevisionTargetBatch(target=target, issue_ids=[issue.issue_id], issue_snapshots=[issue],
                                completion_criteria=issue.completion_criteria)
    plan = ApprovedRevisionPlan(project_id=p.project_id, review_version=1, revision_cycle=1,
                                approved_issues=[issue.issue_id], target_batches=[batch])
    p.phase = ResearchPhase.REVISION
    p.planning_only = False
    p.approved_revision_plans = [plan]
    p.active_revision_plan_id = plan.revision_plan_id
    p.revision_issues = [issue]
    p.revision_migration_version = 5
    p.computation = {"status": "review_required", "rounds": []}
    job = api.jobs.create(p.project_id, p.phase.value)
    job.status = "running"
    api.jobs.save(job)
    p.active_job_id = job.job_id
    api.o.store.save(p)
    api.monkeypatch.setattr(api.o, "_run_bound_internal_executor", lambda *a: pytest.fail("Code executed without approval"))
    api.monkeypatch.setattr(api.o, "_structured_client_for_project", lambda *a: pytest.fail("Dispatch unexpectedly called model"))

    result = api.o.run_next_step(p.project_id, job_id=job.job_id)
    saved = api.o.store.load(p.project_id)
    assert result["stage_status"] == "revision_execution_required"
    assert saved.phase == ResearchPhase.EXECUTION_WAITING
    assert saved.computation["pending_revision_request"] == {
        "revision_plan_id": plan.revision_plan_id, "batch_id": batch.batch_id}
    assert saved.approved_revision_plans[0].target_batches[0].status != "completed"
    assert saved.revision_verifications == []
    assert not saved.computation.get("approved_plan_id")
    assert saved.computation["rounds"] == []
    # Rendering/reloading must not misclassify this intentional pause as an orphan.
    assert api.o.get_project(p.project_id).phase == ResearchPhase.EXECUTION_WAITING


def local_execution_fixture(api):
    """Audited files in a temporary project; never invoke or pretend to test a sandbox."""
    from src.ai_scientist.computation import ComputationPlan

    code = 'import json\nprint("fixture only")\n'
    plan = ComputationPlan(decision="execute", rationale="A local test fixture", method="Compare paired values",
        assumptions=[], input_files=[], expected_outputs=["result.json", "comparison.csv"], required_metrics=["value"],
        validation_checks=["Compare actual values"], stopping_rule="One calculation", code=code)
    run_id = "fixture-run"
    root = api.o.store.project_dir(api.project.project_id)
    output = root / "computation" / run_id / "output"
    output.mkdir(parents=True)
    program = output.parent / "program.py"
    program.write_text(code, encoding="utf-8")
    manifest = []
    for name, content in {"result.json": json.dumps({"metrics": {"value": 7}}), "comparison.csv": "method,value\nA,7\nB,9\n"}.items():
        path = output / name
        path.write_text(content, encoding="utf-8")
        data = path.read_bytes()
        manifest.append({"relative_path": str(path.relative_to(root)), "checksum_sha256": hashlib.sha256(data).hexdigest(),
                         "size_bytes": len(data)})
    audit = {"status": "success", "run_id": run_id, "result": {"metrics": {"value": 7}},
             "code_path": str(program.relative_to(root)), "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
             "artifacts": manifest, "seed": 42}
    return plan, audit


def test_api_reply_then_real_resume_assessment_preserves_execution_fingerprint(api):
    from src.ai_scientist.computation import ComputationService, ComputationPlan, ComputationAssessment
    from src.ai_scientist.schemas import utc_now
    from src.ai_scientist.structured_client import StructuredCallMetadata
    from src.ai_scientist.tools.computation_sandbox import ComputationSandbox

    api.monkeypatch.setenv("AI_SCIENTIST_ENABLE_COMPUTATION", "1")
    api.monkeypatch.setattr("src.ai_scientist.computation.runtime_status", lambda: {"available": True})
    p = api.o.get_project(api.project.project_id)
    p.phase, p.planning_only, p.approval_status = ResearchPhase.EXECUTION_WAITING, False, "valid"
    api.o.store.save(p)
    plan, audit = local_execution_fixture(api)
    service = ComputationService(api.o)
    api.monkeypatch.setattr(main_api, "computation_service", service)
    execution_calls, assessments = [], []

    def fake_execution(*args):
        execution_calls.append(args)
        return audit

    def model(project, instructions, payload, schema, artifact_type):
        if schema == ComputationPlan:
            return plan
        assessments.append(payload)
        if len(assessments) == 1:
            metadata = StructuredCallMetadata(agent_name="skeptical_reviewer", requested_model="fake-qwen",
                actual_model="fake-qwen", fallback_used=False, started_at=utc_now(), finished_at=utc_now(),
                attempted_calls=1, successful_calls=1, public_response="请说明指标的单位。",
                human_input_request="指标是秒还是毫秒？", call_id="ask-unit")
            api.o._pause_for_model_input(project, {"draft": "Need unit before interpretation"}, metadata)
        return ComputationAssessment(decision="stop", addresses_question=True, interpretation="指标为秒；已比较两种方法。",
                                     limitations=["Only the supplied fixture"], next_question="")

    api.monkeypatch.setattr(ComputationSandbox, "execute", fake_execution)
    api.monkeypatch.setattr(service, "call", model)
    pending = service.prepare(p.project_id)
    with pytest.raises(ModelInputRequired):
        service.execute(p.project_id, pending["plan_id"])
    checkpoint = api.o.get_project(p.project_id).computation["awaiting_assessment"].copy()
    reply = api.client.post(f"/api/research/{p.project_id}/reply", json={"request_id": "ask-unit", "response": "单位是秒。"})
    assert reply.status_code == 200
    answered = api.o.get_project(p.project_id)
    assert answered.revision_feedback == p.revision_feedback
    assert service.context(answered, include_human_dialogue=False)[2] == checkpoint["execution_input_fingerprint"]

    queued = api.client.post(f"/api/research/{p.project_id}/computation/resume_assessment")
    assert queued.status_code == 202, queued.text
    api.threads[-1].run()
    job = api.jobs.load(p.project_id, queued.json()["job_id"])
    assert job.status == "completed", job.error
    assert job.result["status"] == "review_required"
    assert len(execution_calls) == 1  # Reply/interpretation never silently reruns code.
    assert len(assessments) == 2
    feedback = assessments[-1]["research"]["shared_research_context"]["human_feedback"]
    assert feedback["human_dialogue"]["responses"][0]["response"] == "单位是秒。"
    result = api.o.get_project(p.project_id)
    assert result.phase == ResearchPhase.DATA_ANALYSIS
    assert "awaiting_assessment" not in result.computation
    assert len(result.computation["rounds"]) == 1
    assert result.computation["rounds"][0]["attempts"][0]["run_id"] == audit["run_id"]


@pytest.mark.parametrize("semantic_pass,tampered", [(True, False), (False, False), (True, True)])
def test_execution_revision_verifies_real_outputs_and_keeps_failures_open(api, semantic_pass, tampered):
    from src.ai_scientist.agents.base_agent import AgentRun
    from src.ai_scientist.schemas import RevisionCriterionResult, RevisionVerificationResult, utc_now
    from src.ai_scientist.structured_client import StructuredCallMetadata

    p = api.o.get_project(api.project.project_id)
    issue = RevisionIssue(target="result_table", classification="plan_blocking", problem="结果表必须来自实际运行",
                          completion_criteria=["比较表包含两种方法的真实值"], status="approved")
    batch = RevisionTargetBatch(target="result_table", issue_ids=[issue.issue_id], issue_snapshots=[issue],
                                completion_criteria=issue.completion_criteria)
    approved = ApprovedRevisionPlan(project_id=p.project_id, review_version=1, revision_cycle=1,
                                    approved_issues=[issue.issue_id], target_batches=[batch])
    plan, audit = local_execution_fixture(api)
    request = {"revision_plan_id": approved.revision_plan_id, "batch_id": batch.batch_id}
    p.phase, p.planning_only, p.revision_migration_version = ResearchPhase.REVISION, False, 5
    p.active_revision_plan_id, p.approved_revision_plans, p.revision_issues = approved.revision_plan_id, [approved], [issue]
    p.computation = {"status": "review_required", "rounds": [{"round": 1, "plan": plan.model_dump(),
        "attempts": [audit], "assessment": {"interpretation": "Recorded comparison"}, "revision_request": request}]}
    job = api.jobs.create(p.project_id, p.phase.value)
    job.status = "running"
    api.jobs.save(job)
    p.active_job_id = job.job_id
    api.o.store.save(p)
    observed = []

    def verify(self, project, target_batch, issues, old_artifact, new_artifact, artifact_version, deterministic_results):
        observed.append(new_artifact)
        output = RevisionVerificationResult(action_id=target_batch.batch_id, target_artifact="result_table", artifact_version=1,
            criteria_results=[RevisionCriterionResult(criterion=issue.completion_criteria[0], passed=semantic_pass,
                                                      evidence="Inspected the supplied actual CSV observation")],
            overall_passed=semantic_pass, verification_method="fake_independent_model")
        metadata = StructuredCallMetadata(agent_name="revision_verifier", requested_model="fake-qwen", actual_model="fake-qwen",
            fallback_used=False, started_at=utc_now(), finished_at=utc_now(), attempted_calls=1, successful_calls=1)
        return AgentRun(output=output, metadata=metadata)

    api.monkeypatch.setattr("src.ai_scientist.orchestrator.RevisionVerifierAgent.verify", verify)
    if tampered:
        path = api.o.store.project_dir(p.project_id) / audit["artifacts"][1]["relative_path"]
        path.write_text("method,value\nA,0\nB,0\n", encoding="utf-8")
    result = api.o.run_next_step(p.project_id, job_id=job.job_id)
    saved = api.o.store.load(p.project_id)
    assert len(observed) == 1
    actual = observed[0]
    assert actual["executed_code"] == plan.code
    assert actual["execution"]["run_id"] == audit["run_id"]
    assert actual["approved_revision_request"] == request
    inspected = actual["actual_artifacts"]
    if not tampered:
        csv = next(item for item in inspected["artifacts"] if item.get("filename") == "comparison.csv")
        assert csv["integrity_verified"]
        assert json.loads(csv["preview"])["rows"] == [["A", "7"], ["B", "9"]]
    else:
        assert inspected["delivery_validation"]["status"] == "needs_revision"

    should_pass = semantic_pass and not tampered
    assert saved.revision_verifications[-1].overall_passed is should_pass
    saved_batch = saved.approved_revision_plans[0].target_batches[0]
    if should_pass:
        assert result["stage_status"] == "revision_cycle_completed"
        assert saved.phase == ResearchPhase.CRITICAL_REVIEW
        assert saved_batch.status == "completed"
        assert saved.approved_revision_plans[0].status == "completed"
        assert saved.active_revision_plan_id is None
    else:
        assert result["stage_status"] == "revision_needs_attention"
        assert saved.phase == ResearchPhase.HUMAN_REVISION_REVIEW
        assert saved_batch.status == "needs_attention"
        assert saved.approved_revision_plans[0].status == "needs_attention"
        assert saved.revision_issues[0].status != "completed"
    assert api.o.get_project(p.project_id).phase == saved.phase
