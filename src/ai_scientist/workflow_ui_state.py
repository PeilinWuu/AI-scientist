"""Resolve one user-facing next action for the fixed research workflow.

This is navigation, not execution authorization. The API remains responsible
for checking approvals, live tool availability, code hashes and input changes.
Keeping this independent of Streamlit lets all entry points use the same
priority order instead of exposing competing workflow controls.
"""
from __future__ import annotations


_ARTIFACT_TARGETS = {'execution_code', 'result_table', 'figure', 'reproducibility_bundle'}
_TERMINAL_ACTIONS = {
    'COMPLETED': ('completed', '查看研究成果', '本次工作流已结束；请查看结论、证据、局限与可下载产物。完成工作流不代表所有科学结论均已证实。'),
    'FAILED': ('failed', '查看失败原因', '项目已停止。请查看失败记录并处理原因；不会通过继续按钮绕过失败状态。'),
    'CANCELLED': ('cancelled', '项目已取消', '此项目不会继续执行；已有记录与产物仍可查看。'),
}


def _action(kind: str, label: str, description: str) -> dict[str, str]:
    return {'kind': kind, 'label': label, 'description': description}


def active_human_question(project: dict) -> dict:
    pending = (project.get('human_dialogue') or {}).get('pending') or {}
    if project.get('phase') not in _TERMINAL_ACTIONS:
        return pending
    messages = (project.get('human_dialogue') or {}).get('messages') or []
    if pending.get('dialogue_origin') or (messages and messages[-1].get('status') == 'needs_input'):
        return pending
    return {}  # Old workflow questions cannot reopen a terminal project.


def _authorized_artifact_revision(project: dict, request: object) -> bool:
    """Mirror the saved-approval eligibility in ComputationService.

    A pending request alone is not authorization. In particular, completed
    plans, unrelated projects and unapproved issue IDs must not unlock another
    computation round in the UI.
    """
    if not isinstance(request, dict):
        return False
    plan_id, batch_id = request.get('revision_plan_id'), request.get('batch_id')
    if not plan_id or not batch_id or project.get('active_revision_plan_id') != plan_id:
        return False
    plan = next((item for item in project.get('approved_revision_plans') or []
                 if item.get('revision_plan_id') == plan_id), None)
    if (not plan or plan.get('project_id') != project.get('project_id')
            or plan.get('created_by') != 'human'
            or plan.get('status') not in {'approved', 'in_progress', 'needs_attention'}):
        return False
    batch = next((item for item in plan.get('target_batches') or []
                  if item.get('batch_id') == batch_id), None)
    if not batch or batch.get('target') not in _ARTIFACT_TARGETS:
        return False
    issues = batch.get('issue_ids') or []
    return (batch.get('status') in {'pending', 'in_progress', 'needs_attention'}
            and bool(issues) and set(issues).issubset(set(plan.get('approved_issues') or [])))


def next_research_action(project: dict, job: dict | None = None) -> dict[str, str]:
    """Return exactly one ``kind``, Chinese ``label`` and ``description``.

    The current job takes precedence while it is queued/running. A failed
    *job* does not make a recoverable *project* terminal. A pending question
    then overrides all ordinary actions, including computational approval.
    ``job`` may be omitted when no job record has been loaded; a saved active
    job ID keeps the UI in its waiting state until that record is resolved.
    """
    phase = project.get('phase', '')
    phase = getattr(phase, 'value', phase)
    matching_job = job if job and (
        not job.get('project_id') or not project.get('project_id')
        or job['project_id'] == project['project_id']) else None
    unresolved_active = project.get('active_job_id') and (
        matching_job is None or (matching_job.get('job_id')
                                 and matching_job['job_id'] != project['active_job_id']))
    if matching_job and matching_job.get('status') == 'unavailable':
        return _action('running', '正在确认任务状态', '暂时无法读取现有任务状态。请刷新后再操作，避免重复启动或在任务运行中修改项目。')
    if (matching_job and matching_job.get('status') in {'queued', 'running'}) or unresolved_active:
        if matching_job and matching_job.get('pause_requested'):
            return _action('running', '正在保存进展并暂停', '已收到暂停请求；当前请求不会被强行中断。背景检索在当前动作后停止，其他阶段在阶段结束后停止。')
        return _action('running', '正在研究', '可暂停以补充要求；背景检索会保存当前动作再停止，无需重复启动。')

    pending = active_human_question(project)
    if phase == 'COMPLETED' and pending:
        return _action('reply', '回答 Qwen 的问题', '补充当前讨论所需的信息；不会自动重开研究或批准执行。')
    if phase in _TERMINAL_ACTIONS:
        return _action(*_TERMINAL_ACTIONS[phase])

    if (project.get('human_dialogue') or {}).get('pending'):
        return _action('reply', '回答 Qwen 的问题', '请先回答下面的必要问题。提交后将从暂停处继续，不会跳过审批或重新执行已完成的计算。')

    if phase == 'SEARCH_PLAN_REVIEW':
        if (project.get('background_agent') or {}).get('status') == 'budget_paused':
            return _action('evidence_review', '确认是否追加研究额度', '已有资料与权限保留；这里仅决定追加动作和模型额度，不代表重新批准研究结论。')
        return _action('evidence_review', '确认资料检索计划', '请检查检索范围与资料需求，再决定是否采用此计划；此处不会自动确认资料结论。')
    if phase == 'HUMAN_SOURCE_REVIEW':
        if (project.get('background_agent') or {}).get('status') == 'needs_input':
            return _action('evidence_review', '研究已暂停，选择下一步', '可整理已有资料、提供新来源或补充要求；工具故障不等于需要你批准证据，也不必强行采用资料。')
        return _action('evidence_review', '确认采用的资料', '请查看来源、相关性与读取内容，再确认采用或要求调整。没有合适资料时，请明确说明局限，不必为继续而强行采用。')
    if phase == 'HUMAN_REVISION_REVIEW':
        return _action('revision_review', '处理审查意见', '请逐项决定修订、暂缓或接受为局限，确认后才会执行对应修订；不会自动批准新代码。')
    if phase == 'HUMAN_APPROVAL':
        return _action('approval', '审阅并确认研究方案', '请核对问题、方法、数据和风险，再明确批准或提出修改。方案获批不等于计算代码获批。')
    if phase == 'EXECUTION_WAITING' and project.get('research_mode') == 'systematic_review' and not project.get('planning_only'):
        literature = project.get('literature_review') or {}
        if literature.get('pending_operation'):
            return _action('continue', '继续处理已保存的请求', '你的要求已保存。继续处理这次修改或核查，无需重新提交，也不重新检索。')
        if (literature.get('draft') or {}).get('report_markdown'):
            return _action('literature_draft', '研究回答已保存', '先阅读下方回答；可以按需核查、提出修改，或保留当前核查状态结束本轮。不必反复重跑整个阶段。')
        return _action('continue', '继续文献证据综合', '基于已采用的原文直接生成可读回答；核查可按需追加，不自动运行 Python 或声称完成实验。')
    state = project.get('computation') or {}
    computation_input_recovery = (
        phase == 'HUMAN_INTERVENTION_REQUIRED'
        and state.get('status') == 'needs_input'
        and project.get('approval_status') == 'valid'
        and not project.get('planning_only', True)
        and not project.get('executor_binding')
        and 'isolated_scientific_python' in (project.get('available_tools') or [])
    )
    if phase == 'HUMAN_INTERVENTION_REQUIRED' and not computation_input_recovery:
        return _action('intervention', '处理当前阻碍', '自动研究已暂停。请查看具体阻碍，补充资料、修改研究方案或处理审查意见后再继续。')

    if phase in {'EXECUTION_WAITING', 'EXECUTION'} or computation_input_recovery:
        # A planning-only project must not expose controls that run real code,
        # even if an old computation record remains attached to the project.
        if project.get('planning_only', True):
            if phase == 'EXECUTION_WAITING':
                return _action('continue', '继续整理研究方案', '当前为仅规划模式；继续将整理研究结论与计划，不会执行实验或生成实验结果。')
            return _action('intervention', '检查执行状态', '项目设为仅规划，但记录停在执行阶段。请先核对项目状态，不会自动执行代码。')
        if project.get('execution_capability') == 'INTERNAL_EXECUTABLE' and project.get('executor_binding'):
            if phase == 'EXECUTION_WAITING':
                return _action('execute_bound', '准备项目内执行', '该项目使用已绑定的执行器。此步只进入执行确认阶段，不会运行代码；下一步需明确确认开始。')
            return _action('execute_bound', '确认并开始项目内执行', '该项目使用已绑定的执行器。确认后才会运行实验或数据分析，并保存真实执行记录。')
        if phase == 'EXECUTION':
            return _action('intervention', '检查中断的执行', '当前没有运行中的任务，也没有可启动的绑定执行器。请检查执行记录后恢复，不会重复启动未知状态的计算。')
        if state.get('awaiting_assessment'):
            return _action('resume_assessment', '继续评估已有结果', '计算结果已保存；只恢复 Qwen 对已有结果的评估，不会重复执行计算。恢复前仍会核验方案、输入与产物。')
        if state.get('status') == 'awaiting_approval':
            return _action('computation_approval', '审阅并批准计算', '请检查本次方法、输入、指标、验证步骤与代码，确认后才会执行。此前的方案审批不能替代本次代码审批。')
        if project.get('executor_binding'):
            return _action('intervention', '检查执行器配置', '项目记录中存在执行器绑定，但当前执行能力不匹配。请核对配置，不会改用另一种执行方式。')
        if 'isolated_scientific_python' not in (project.get('available_tools') or []):
            return _action('external_data', '补充实验数据或执行结果', '当前没有可用的项目内执行工具。请上传实际数据或外部实验结果，不能把规划文字当作实验结果。')

        request = state.get('pending_revision_request') or state.get('revision_request')
        revision_authorized = _authorized_artifact_revision(project, request)
        if request and not revision_authorized:
            return _action('intervention', '重新确认修订授权', '此计算关联的修订批次尚未获有效人工批准，或已经处理完毕。请先核对审查意见与修订计划。')
        if project.get('approval_status') != 'valid' and not revision_authorized:
            return _action('intervention', '重新确认研究方案', '当前研究方案尚未获有效审批或审批已过期。请先核对方案与批准状态，再生成计算代码。')
        rounds = state.get('rounds') or []
        if len(rounds) >= 2 and not revision_authorized:
            return _action('intervention', '确认后续研究范围', '当前已用完两轮科学计算。若需修复交付产物，请先批准对应修订；扩大研究范围需另行规划。')
        if (rounds and not revision_authorized
                and (rounds[-1].get('assessment') or {}).get('decision') != 'iterate'
                and state.get('status') != 'needs_input'):
            return _action('intervention', '补充资料并确认下一步', '上一轮未建议继续计算。请先补充必要信息或明确修订要求，不能重复运行同一方案来代替科学判断。')
        if state.get('status') == 'unsupported' and not revision_authorized:
            return _action('prepare_computation', '补充或调整后重新生成计算方案', '现有方案超出当前工具能力。请先依据 Qwen 的说明补充资料、调整方案或提供外部结果，再请求重新评估；此操作不执行代码，也不保证调整后一定可计算。')
        if state.get('status') == 'needs_input' and not state.get('awaiting_model_operation') and not revision_authorized:
            # Uploading data deliberately preserves the computation status. A
            # permanent intervention here would strand a project even after
            # the user supplies exactly what the model requested. This is an
            # explicit re-planning request, not permission to execute; when a
            # previous scientific round exists the server additionally checks
            # that the scientific-input fingerprint has really changed.
            return _action('prepare_computation', '补充资料后重新生成计算方案', '请先按照 Qwen 的说明补充必要资料或验证条件，再重新生成方案。后端仍会核验审批与输入变化；新代码必须另行审阅和批准，不会直接重复计算。')
        if revision_authorized:
            return _action('prepare_computation', '生成修订计算方案', '根据你已批准的产物修订要求生成新方案与代码；生成后仍需单独审阅并批准执行。')
        return _action('prepare_computation', '生成计算方案', 'Qwen 将依据已批准的研究方案与真实资料生成方法和代码；此操作不执行代码，生成后会停下请你审阅。')

    if matching_job and matching_job.get('status') == 'failed':
        return _action('continue', '重试当前研究阶段', '上次任务未完成，项目记录已保留。请查看错误原因后从当前阶段重试，仍会在必要问题和人工审批处暂停。')
    if phase == 'INTAKE':
        return _action('continue', '开始研究', '项目已创建，尚未开始研究。点击后由 Qwen 按固定科研流程推进，在必要问题和人工审批处暂停。')
    return _action('continue', '继续研究', '自动衔接固定科研流程，直到需要你回答问题、审阅资料、批准方案或确认执行。')
