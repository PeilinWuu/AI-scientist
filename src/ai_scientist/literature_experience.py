"""Draft-first literature workflow. Review is an explicit, resumable operation."""
import hashlib
import json

from src.ai_scientist.background_agent import BackgroundAgent, assessment_passages
from src.ai_scientist.grounded_audit import grounded_review, AuditProtocolError
from src.ai_scientist.schemas import Conclusion, utc_now
from src.ai_scientist.structured_client import MarkdownDraft


def validate_approval(orchestrator, project):
    if (project.approval_status != 'valid' or project.approval_valid_for_versions !=
            orchestrator._review_artifact_versions(project)):
        raise ValueError('研究方案已变化或尚未批准，请先确认当前研究方案。')
    if not project.evidence:
        raise ValueError('尚无已采用的研究依据，请先确认资料。')


def source_context(project):
    """Only actual read text, deduplicated; omissions are explicit, never negative evidence."""
    sources = []
    remaining = 54000
    for evidence in project.evidence:
        source = next((s for s in project.background_agent.get('sources', [])
                       if s.get('url') == evidence.source_url), {})
        passages = list(dict.fromkeys(assessment_passages(source, evidence.source_quote or '')))
        allowance = min(18000, remaining // max(1, len(project.evidence) - len(sources)))
        selected = []
        for passage in passages:
            if allowance <= 0:
                break
            text = passage[:allowance]
            selected.append(text)
            allowance -= len(text)
            remaining -= len(text)
        sources.append({'evidence_id': evidence.evidence_id, 'title': evidence.title,
                        'url': evidence.source_url, 'actual_read_passages': selected,
                        'omitted_chars': sum(map(len, passages)) - sum(map(len, selected))})
    return sources


def _fingerprint(project, sources):
    return hashlib.sha256(json.dumps({'objective': project.objective, 'constraints': project.constraints,
        'versions': project.approval_valid_for_versions, 'sources': sources},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def current_draft(state):
    return (state.get('draft') or {}).get('report_markdown') or state.get('report_markdown', '')


def approved_writing_brief(project):
    """Carry approved decisions forward without treating them as primary evidence."""
    data = project.model_dump(mode='json')
    return {key: data.get(key) for key in (
        'study_design', 'analysis_plan', 'method_rationale', 'validity_threats',
        'required_controls', 'reproducibility_plan', 'revision_feedback')} | {
        'latest_plan_review': data.get('reviews', [])[-1:],
        'rule': 'These are approved scope/method decisions and fallible model assessments, '
                'not primary evidence. Preserve applicable limits and deliverables. Verify '
                'facts against actual_read_passages. Explain necessary departures from the plan.'}


def run_draft(orchestrator, project):
    validate_approval(orchestrator, project)
    state = project.literature_review
    sources = source_context(project)
    fingerprint = _fingerprint(project, sources)
    # Old failed reviews are shown without silently rewriting or certifying them.
    if current_draft(state) and state.get('experience_fingerprint') in {None, fingerprint} and not state.get('revision_request'):
        state.update(status='draft_ready', experience_fingerprint=fingerprint)
        orchestrator.store.save(project)
        return []
    state.update(status='synthesizing', experience_fingerprint=fingerprint)
    orchestrator.store.save(project)
    payload = {'question': project.objective, 'requirements': project.constraints,
               'approved_writing_brief': approved_writing_brief(project),
               'sources': sources, 'user_revision_request': state.get('revision_request', ''),
               'previous_draft': current_draft(state) if state.get('revision_request') else '',
               'review_advice_not_evidence': state.get('audit') if state.get('revision_request') else None,
               'source_rule': 'Only actual_read_passages are primary text. Missing or omitted passages are not evidence of absence.'}
    agent = BackgroundAgent(orchestrator)
    result = agent.call(project, MarkdownDraft, payload, role='scientific_synthesizer', instructions_override=
        'You are the researcher carrying out the approved bounded literature study. Answer the question directly '
        'in natural, readable Markdown, using the supplied sources. Organize the answer yourself; use a comparison '
        'table if helpful and link sources beside relevant claims. Explain the main answer, evidence, disagreements '
        'and uncertainties. Distinguish observed behavior, measured outcomes and proposed mechanisms. '
        'Lead with a short accessible answer and material evidence limits. Be concise unless '
        'the user asks for depth; do not repeat limitations across sections. Respect requested '
        'length and table count. Preserve qualifications in the opening, tables and conclusion, '
        'not only an end disclaimer. Follow the approved writing brief. '
        'Do not manufacture experiments, unread facts, exhaustive coverage or validation certificates. '
        'This is a useful research draft, not a schema-filling exercise. If information is missing, say which '
        'conclusions remain open; still deliver what the evidence supports. Follow user revision requests '
        'without blindly treating prior model reviews as source evidence. No JSON, no fixed headings, no private reasoning.')
    old = current_draft(state)
    if old:
        state.setdefault('draft_history', []).append({'report_markdown': old, 'saved_at': utc_now().isoformat(),
            'review_status': state.get('review_status'), 'review_notes': state.get('review_notes', []),
            'audit': state.get('audit')})
    state.update(status='draft_ready', draft={'report_markdown': result.text},
                 review_status='not_requested', revision_request='', review_notes=[],
                 audit_checkpoints={}, last_error=None)
    state.pop('generation_error', None)
    state.pop('audit', None)
    state.pop('review_error', None)
    state.pop('human_delivery_acknowledged', None)
    record = orchestrator.artifacts.save_named_text(project.project_id, 'literature_draft.md',
        'literature_draft', result.text, 'scientific_synthesizer')
    project.artifacts.append(record)
    orchestrator.store.save(project)
    return [record.artifact_id]


def review_draft(orchestrator, project):
    validate_approval(orchestrator, project)
    state = project.literature_review
    draft = current_draft(state)
    if not draft:
        raise ValueError('请先生成研究回答，再补充核查。')
    sources = source_context(project)
    if state.get('experience_fingerprint') not in {None, _fingerprint(project, sources)}:
        raise ValueError('研究依据已变化，请先按新资料修改回答，再补充核查。')
    passages = [p for source in sources for p in source['actual_read_passages']]
    key = hashlib.sha256(json.dumps({'draft': draft, 'sources': sources, 'policy': 2},
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    checkpoint = state.setdefault('audit_checkpoints', {}).get(key)
    state.update(status='auditing', review_status='running')
    orchestrator.store.save(project)
    try:
        if checkpoint is None:
            review = grounded_review(BackgroundAgent(orchestrator), project,
                {'proposed_assessment': draft, 'actual_read_passages': passages,
                 'source_index': [{k: s[k] for k in ('evidence_id', 'title', 'url', 'omitted_chars')} for s in sources]}, max_attempts=1, instructions=
                'Check this answer in one pass across sources. Focus on material factual errors, '
                'misattributed numbers and conclusions exceeding evidence, not style or missing optional detail. '
                'Do not demand new research merely to finish a bounded answer. No automatic rewriting.')
            checkpoint = review.model_dump()
            state['audit_checkpoints'][key] = checkpoint
        state.update(status='draft_ready', review_status='checked' if checkpoint['supported'] else 'issues_found',
                     review_notes=[checkpoint['rationale']], audit=checkpoint)
    except AuditProtocolError as exc:
        state.update(status='draft_ready', review_status='unavailable', review_notes=[
            '核查模型的引文未能定位到提供的原文。这不等于你的研究失败；草稿已保留，未按这条意见自动改写。'],
            review_error=str(exc))
    except Exception:
        state.update(status='draft_ready', review_status='unavailable', review_notes=[
            '这次补充核查没有完成，草稿仍可阅读和下载。可以稍后再核查或保留此限制结束本轮。'])
        orchestrator.store.save(project)
        raise
    if state.get('review_status') != 'unavailable':
        state.pop('review_error', None)
    orchestrator.store.save(project)
    return []


def finish_draft(orchestrator, project):
    validate_approval(orchestrator, project)
    state = project.literature_review
    if not state.get('human_delivery_acknowledged'):
        raise ValueError('请先确认保留当前核查状态结束本轮。')
    if state.get('experience_fingerprint') not in {None, _fingerprint(project, source_context(project))}:
        raise ValueError('研究依据已变化，请先修改回答，不会将过期草稿标记为本轮结果。')
    draft = current_draft(state)
    if not draft:
        raise ValueError('没有可保存的研究回答。')
    state.update(status='complete', report_markdown=draft, delivery_policy='draft_first',
                 completed_at=utc_now().isoformat())
    state.setdefault('review_status', 'not_requested')
    record = orchestrator.artifacts.save_named_text(project.project_id, 'literature_review.md',
        'literature_review', export_report(state), 'scientific_synthesizer')
    project.artifacts.append(record)
    return [record.artifact_id]


def export_report(state):
    status = {'checked': '已完成一次模型辅助核查（不代表科学结论已证实）',
              'issues_found': '核查发现待处理意见', 'unavailable': '补充核查未完成',
              'not_requested': '未进行补充核查'}.get(state.get('review_status'), '未完成最终核查')
    notes = state.get('review_notes') or []
    legacy = state.get('last_error') or {}
    if not notes and isinstance(legacy, dict) and legacy.get('rationale'):
        notes = [legacy['rationale']]
    return current_draft(state) + '\n\n---\n核查状态：' + status + (
        '\n\n待核对意见（不是新的原始证据）：\n' + '\n\n'.join(notes) if notes else '')


def delivery_conclusion(project):
    """No second model rewrite: the already-visible answer is the delivered answer."""
    state = project.literature_review
    return Conclusion(planning_status_statement='已保存本轮限定范围文献研究；未开展新实验。科研结论及核查局限以原回答为准。',
                      limitations=state.get('review_notes') or [],
                      human_verification_required=[] if state.get('review_status') == 'checked' else ['报告仍有未完成或待处理的核查事项。'])
