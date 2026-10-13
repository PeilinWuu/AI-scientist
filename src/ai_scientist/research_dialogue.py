"""A single conversation entry, with model interpretation and explicit scope gates.

The model chooses an intent, not permissions. This module never executes code,
adopts sources, raises budgets or approves research on the user's behalf.
"""
from copy import deepcopy
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

from src.ai_scientist.background_agent import BackgroundAgent
from src.ai_scientist.exceptions import ModelInputRequired
from src.ai_scientist.literature_experience import current_draft, validate_approval
from src.ai_scientist.schemas import ResearchPhase, new_id, utc_now
from src.ai_scientist.research_context import adopted_source_passages


class DialogueDecision(BaseModel):
    public_response: str = Field(min_length=1)
    action: Literal['discuss', 'revise_answer', 'review_answer', 'background_followup',
                    'restart_sources', 'revise_plan', 'revise_code', 'remember', 'deliver_plan']
    target: Literal['question', 'evidence', 'hypothesis', 'method', 'design', 'analysis',
                    'reproducibility'] = 'design'
    instructions: str = ''


INSTRUCTIONS = '''Respond directly to the researcher in natural Markdown, with an appropriate
length and structure for their actual question. You are in an existing fixed research workflow,
not a new autonomous workspace. Use actual supplied context; distinguish recorded evidence
from your interpretation. Do not expose private reasoning. Never claim unperformed tool work.
Choose only from allowed_actions. discuss answers a question without changing the research.
revise_answer is ONLY for an explicit request to edit the existing answer using existing
approved sources; explain briefly what you will change, a separate writer updates the document.
review_answer is ONLY for an explicit request to check the current answer against existing sources.
Requests for NEW sources, experiments, or changed research scope must NOT be treated as mere
rewriting. Use background_followup for more work inside the current authorized background scope,
restart_sources for a new source scope, revise_plan for a planning change, revise_code for
feedback on unexecuted code. These are proposals: explain the effect and say confirmation is
needed, not that the change has happened. No action can authorize network, code, spending limits
or adopt sources. remember stores an explicit requirement for upcoming stages, without claiming
it has been implemented. Ambiguous requests should use discuss and ask a concise clarification.
instructions must faithfully include the user's requirements and your proposed scope, not add
unrequested work. Do not rewrite a whole report in public_response when choosing revise_answer.
revise_answer and review_answer already have the user's explicit request: they run immediately
after this response, within existing scope and budget. Do NOT ask for another confirmation or
say 'confirm to proceed' for these two actions. Say the answer is being updated/checked; do not
claim success before the writer/reviewer returns. Other change proposals require confirmation.
Use human_input_request only for genuinely missing details, never for authorization. Research
scope proposals use their action and the confirmation card instead of asking for a free-text
approval. If details are missing, explain the question in public_response too.
deliver_plan is for an explicit request to finish THIS round with literature explanation and
the existing study design, leaving real measurements/computation for later. It proposes a
scope reduction, not fake execution. Prefer it over remember when the user asks to stop
waiting for measurements and deliver the plan. A confirmation card will apply the change;
say the final report will then be generated, NEVER that it is already saved or completed.
If this action is unavailable, explain the actual workflow limitation instead of claiming
you changed the phase. discuss/remember cannot complete a project. Check current phase
against any earlier conversational claims of completion; the recorded phase is authoritative.
Source-specific statements must use adopted_source_read_receipts, not earlier assistant
answers. If a user's challenge reveals an unsupported comparison or outcome, correct it
plainly; do not invent an explanation to preserve the earlier answer.
'''


def allowed_actions(project):
    actions = ['discuss', 'remember']
    phase = project.phase.value
    if phase in {'FAILED', 'CANCELLED'}:
        return ['discuss']
    state = project.literature_review
    if (current_draft(state) and project.research_mode.value == 'systematic_review'
            and (phase == 'EXECUTION_WAITING' or
                 (phase == 'COMPLETED' and state.get('delivery_policy') == 'draft_first'))):
        actions += ['revise_answer', 'review_answer']
    if project.research_policy_version == 2 and phase in {
            'BACKGROUND_RESEARCH', 'SEARCH_PLAN_REVIEW', 'HUMAN_SOURCE_REVIEW'}:
        actions += ['background_followup', 'restart_sources']
    if phase == 'HUMAN_APPROVAL':
        actions += ['revise_plan']
    if can_deliver_plan(project):
        actions += ['deliver_plan']
    if project.computation.get('status') == 'awaiting_approval' and phase == 'EXECUTION_WAITING':
        actions += ['revise_code']
    # Reopening a finished literature study requires a separate scope confirmation.
    if 'revise_answer' in actions and project.research_policy_version == 2:
        actions += ['restart_sources']
    return actions


def can_deliver_plan(project):
    return (not project.planning_only and bool(project.study_design)
            and project.phase.value in {'HUMAN_APPROVAL', 'EXECUTION_WAITING'}
            and (project.phase == ResearchPhase.HUMAN_APPROVAL or project.approval_status == 'valid')
            and project.internal_execution_summary.get('status') != 'complete'
            and not project.controlled_python_runs
            and not project.computation.get('attempts')
            and not project.computation.get('rounds'))


def context_token(project):
    """Bind a proposed change to the actual phase, input, scope, approval and draft."""
    data = project.model_dump(mode='json')
    fields = {k: data.get(k) for k in ('phase', 'objective', 'constraints', 'evidence',
        'active_artifact_versions', 'approval_status', 'approval_valid_for_versions',
        'background_agent', 'computation', 'planning_only', 'study_design', 'research_mode',
        'internal_execution_summary', 'controlled_python_runs')}
    fields['draft'] = current_draft(project.literature_review)
    fields['pending'] = project.human_dialogue.get('pending')
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reopen_answer(project):
    """Preserve the last delivery; changes are still limited by validate_approval."""
    state = project.literature_review
    if project.phase == ResearchPhase.COMPLETED:
        state.setdefault('delivery_history', []).append({
            'report_markdown': current_draft(state), 'review_status': state.get('review_status'),
            'review_notes': deepcopy(state.get('review_notes', [])),
            'completed_at': state.get('completed_at')})
        project.phase = ResearchPhase.EXECUTION_WAITING
        project.conclusion = None
        state.update(status='draft_ready')
        state.pop('completed_at', None)
        state.pop('human_delivery_acknowledged', None)


def run_dialogue(orchestrator, project_id, message_id):
    project = orchestrator.get_project(project_id)
    entry = next(m for m in project.human_dialogue['messages'] if m['id'] == message_id)
    entry['status'] = 'processing'
    orchestrator.store.save(project)
    actions = allowed_actions(project)
    payload = {'user_message': entry['text'], 'allowed_actions': actions,
        'adopted_source_read_receipts': adopted_source_passages(project),
        'attached_materials': [{'asset_id': a.asset_id, 'filename': a.filename,
                               'purpose': a.purpose, 'parsing_status': a.parsing_status} for a in project.research_assets
                               if a.asset_id in entry.get('attachment_ids', [])],
        'current_answer': current_draft(project.literature_review),
        'recent_conversation': [{k: m.get(k) for k in ('text', 'response', 'status')}
                                for m in project.human_dialogue['messages'][-7:-1]],
        'instruction': 'Source passages and project memory are supplied in shared_research_context.'}
    try:
        decision = BackgroundAgent(orchestrator).call(project, DialogueDecision, payload,
            role='scientific_synthesizer', instructions_override=INSTRUCTIONS)
    except ModelInputRequired:
        entry['status'] = 'needs_input'
        pending = project.human_dialogue.get('pending')
        if pending:
            pending['dialogue_origin'] = message_id
        orchestrator.store.save(project)
        return False
    except Exception:
        entry['status'] = 'failed'
        orchestrator.store.save(project)
        raise
    entry.update(response=decision.public_response, status='answered', action=decision.action,
                 answered_at=utc_now().isoformat())
    # A malformed or unavailable route cannot bypass the current workflow gate.
    if decision.action not in actions:
        entry.update(status='not_applied', notice='这项调整不适用于当前阶段；研究和授权未改变。')
        orchestrator.store.save(project)
        return False
    feedback = entry['text'] + ('\n\nQwen 对请求的理解：\n' + decision.instructions
                               if decision.instructions else '')
    if decision.action in {'revise_answer', 'review_answer'}:
        validate_approval(orchestrator, project)
        if project.budget.attempted_model_calls >= project.budget.max_model_calls:
            entry.update(status='not_applied', notice='请求已理解，但剩余额度不足以继续；原回答未改变。')
            orchestrator.store.save(project)
            return False
        reopen_answer(project)
        state = project.literature_review
        state['pending_operation'] = 'draft' if decision.action == 'revise_answer' else 'review'
        state['dialogue_message_id'] = message_id
        if decision.action == 'revise_answer':
            state['revision_request'] = feedback
            project.revision_feedback.append(feedback)
        entry['status'] = 'applying'
        orchestrator.store.save(project)
        return True
    if decision.action == 'remember':
        project.revision_feedback.append(feedback)
        entry.update(status='saved', notice='要求已保存，后续阶段会收到；尚未据此修改现有成果。')
    elif decision.action != 'discuss':
        proposal = {'id': new_id('proposal'), 'action': decision.action,
                    'target': decision.target, 'feedback': feedback,
                    'message_id': message_id, 'context_token': context_token(project)}
        project.human_dialogue['proposal'] = proposal
        entry['status'] = 'awaiting_confirmation'
    orchestrator.store.save(project)
    return False


def apply_proposal(orchestrator, project, proposal_id, accept):
    proposal = project.human_dialogue.get('proposal') or {}
    if proposal.get('id') != proposal_id:
        raise ValueError('这项建议已处理或已更新，请刷新后查看。')
    entry = next(m for m in project.human_dialogue['messages'] if m['id'] == proposal['message_id'])
    if not accept:
        entry['status'] = 'declined'
        project.human_dialogue.pop('proposal', None)
        orchestrator.store.save(project)
        return False
    if proposal['context_token'] != context_token(project):
        raise ValueError('研究状态已变化，请重新说明调整要求，避免应用过期建议。')
    action, feedback = proposal['action'], proposal['feedback']
    if action not in allowed_actions(project):
        raise ValueError('当前阶段已不支持这项调整。')
    if project.budget.attempted_model_calls >= project.budget.max_model_calls:
        raise ValueError('模型额度已用完，请先明确追加额度。建议尚未应用。')
    if (action == 'deliver_plan' and project.phase == ResearchPhase.EXECUTION_WAITING
            and project.approval_valid_for_versions != orchestrator._review_artifact_versions(project)):
        raise ValueError('研究方案已变化，请重新批准当前方案后再交付。')
    project.human_dialogue.pop('proposal', None)
    entry['status'] = 'confirmed'
    orchestrator.store.save(project)
    if action == 'background_followup':
        BackgroundAgent(orchestrator).feedback(project, feedback)
    elif action == 'restart_sources':
        # Keep prior results as history, never as current evidence in a new scope.
        project.human_dialogue.setdefault('research_history', []).append({
            'saved_at': utc_now().isoformat(), 'literature_review': deepcopy(project.literature_review),
            'evidence': [e.model_dump(mode='json') for e in project.evidence]})
        orchestrator._invalidate_approval(project, feedback)
        orchestrator._targeted_rollback(project, 'evidence', feedback, [])
        if project.literature_review:
            project.literature_review['status'] = 'previous_scope'
            project.literature_review.pop('pending_operation', None)
        project.evidence, project.claims = [], []
        project.conclusion = None
        project.background_agent = {'feedback': [feedback]}
        project.revision_feedback.append(feedback)
        orchestrator.store.save(project)
    elif action == 'revise_plan':
        orchestrator.request_revision(project.project_id, proposal['target'], feedback)
    elif action == 'deliver_plan':
        project.planning_only = True
        project.execution_capability = 'PLANNING_ONLY'
        project.executor_binding = None
        project.constraints['delivery_scope_decision'] = {
            'scope': 'plan_only', 'rationale': feedback, 'decided_by': 'human_confirmation'}
        project.revision_feedback.append(feedback)
        project.human_dialogue.setdefault('deferred_execution', []).append({
            'saved_at': utc_now().isoformat(), 'human_actions_required': list(project.human_actions_required),
            'computation': deepcopy(project.computation)})
        project.human_actions_required = []
        project.computation = {}
        entry.update(status='applying', notice='已确认本轮仅交付资料解释与方案；最终报告尚待生成。')
        orchestrator.store.save(project)
        if project.phase == ResearchPhase.HUMAN_APPROVAL:
            # Do not silently approve the research package when reducing scope.
            entry.update(status='saved', notice='已改为仅交付方案；请确认研究方案后生成最终报告。')
            orchestrator.store.save(project)
            return False
        orchestrator._transition_event(project, 'planning_only')
        orchestrator.store.save(project)
    elif action == 'revise_code':
        state = project.computation
        state.setdefault('human_feedback', []).append({'plan_id': state['plan_id'], 'feedback': feedback})
        state['status'] = 'needs_input'
        orchestrator.store.save(project)
        # Preparing/executing code still uses the existing explicit controls.
        return False
    return True


def settle_dialogue(orchestrator, project_id, failed=False, message_id=None):
    project = orchestrator.get_project(project_id)
    message_id = message_id or project.literature_review.get('dialogue_message_id')
    for entry in project.human_dialogue.get('messages', []):
        if entry['id'] == message_id and entry.get('status') in {'queued', 'processing', 'applying'}:
            if failed:
                entry['status'] = 'not_applied'
                entry['notice'] = '后续操作未完成，原回答保留；可重试，不必重新创建研究。'
            elif project.literature_review.get('pending_operation') or project.human_dialogue.get('pending'):
                entry['status'] = 'saved'
                entry['notice'] = '要求已保存，等待继续处理；现有回答尚未据此更新。'
            else:
                entry['status'] = 'applied'
    orchestrator.store.save(project)
