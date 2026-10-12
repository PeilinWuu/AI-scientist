"""Fixed workflow continuation. This scheduler never grants a human approval."""
from __future__ import annotations

HUMAN_GATES = {'SEARCH_PLAN_REVIEW', 'HUMAN_SOURCE_REVIEW', 'HUMAN_REVISION_REVIEW',
               'HUMAN_APPROVAL', 'HUMAN_INTERVENTION_REQUIRED'}
TERMINAL = {'COMPLETED', 'FAILED', 'CANCELLED'}


def pause_reason(project) -> str | None:
    phase = project.phase.value
    if project.human_dialogue.get('pending'):
        return 'model_requested_input'
    if phase in HUMAN_GATES:
        return 'human_gate'
    if phase in TERMINAL:
        return 'terminal'
    if (phase == 'EXECUTION_WAITING' and project.literature_review.get('status') == 'draft_ready'
            and not project.literature_review.get('pending_operation')):
        return 'literature_draft_ready'
    if phase == 'EXECUTION_WAITING' and project.computation.get('status') == 'awaiting_approval':
        return 'code_approval_required'
    if phase == 'EXECUTION':
        # A bound executor must also be explicitly started, not silently invoked by continuation.
        return 'execution_confirmation_required'
    return None


def progress_signature(project):
    observations = project.background_agent.get('observations', [])
    successes = sum(o.get('status') == 'completed' and (o.get('result') or {}).get('status') not in
                    {'proposal_requires_revision', 'tool_not_executed', 'no_new_sources'} for o in observations)
    return (project.phase.value, successes,
            len(project.background_agent.get('assessments', {})), project.computation.get('plan_id'),
            tuple(sorted(project.active_artifact_versions.items())))


def continue_research(orchestrator, project_id, job_id, *, max_steps=40,
                      should_pause=lambda: False, on_stage=lambda phase: None):
    """Resume approved read/planning work until a meaningful gate or no progress."""
    result = {}
    for index in range(max_steps):
        project = orchestrator.get_project(project_id)
        reason = pause_reason(project)
        if reason or should_pause():
            return {**result, 'current_phase': project.phase.value,
                    'pause_reason': reason or 'user_pause', 'stages_run': index}
        before = progress_signature(project)
        on_stage(project.phase.value)
        result = orchestrator.run_next_step(project_id, job_id=job_id)
        updated = orchestrator.get_project(project_id)
        reason = pause_reason(updated)
        if reason:
            return {**result, 'pause_reason': reason, 'stages_run': index + 1}
        if should_pause():
            return {**result, 'pause_reason': 'user_pause', 'stages_run': index + 1}
        if progress_signature(updated) == before:
            return {**result, 'pause_reason': 'no_progress', 'stages_run': index + 1}
    return {**result, 'pause_reason': 'batch_limit', 'stages_run': max_steps}
