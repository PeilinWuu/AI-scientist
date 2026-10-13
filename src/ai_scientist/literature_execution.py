"""Evidence synthesis is execution too; no fabricated experiment or Python required."""
import hashlib
import json
from typing import Literal
from pydantic import Field
from src.ai_scientist.schemas import StrictModel, ResearchMode, utc_now
from src.ai_scientist.background_agent import (
    BackgroundAgent, SourceAssessmentReview, assessment_passages, SOURCE_REVIEW_INSTRUCTIONS,
)
from src.ai_scientist.tools.research_web import normalize_text
from src.ai_scientist.structured_client import MarkdownDraft
from src.ai_scientist.grounded_audit import grounded_review
from src.ai_scientist.research_context import source_for_evidence


class EvidenceRow(StrictModel):
    evidence_id: str
    question: str
    study_type: str
    population_or_system: str
    intervention_or_exposure: str
    comparator: str
    outcome: str
    finding: str
    certainty: str
    limitations: list[str]
    quote: str = Field(min_length=20, max_length=1200)


class LiteratureReadiness(StrictModel):
    decision: Literal['complete', 'needs_input']
    limitations: list[str]
    open_questions: list[str]
    missing_input: str = ''


class LiteratureOutput(LiteratureReadiness):
    report_markdown: str


def eligible(project):
    return project.research_mode == ResearchMode.SYSTEMATIC_REVIEW and not project.planning_only


def synthesis_rows(project):
    """Audit commentary is not a primary source or an extra evidence table column."""
    return [{k: v for k, v in row.items() if k in EvidenceRow.model_fields or k in {'title', 'url'}}
            for row in project.literature_review.get('rows', [])]


def synthesis_passages(project):
    """Supply read receipts, not guessed details or the complete retrieval cache.

    The request-size guard rejects oversized evidence batches instead of silently
    dropping sources. Larger reviews must reduce the batch before retrying.
    """
    result = []
    for row in synthesis_rows(project):
        source = _source_for_row(project, row)
        result.append({'evidence_id': row['evidence_id'], 'url': row['url'],
                       'actual_read_passages': [t[:12000] for t in assessment_passages(source, row['quote'])]})
    return result


def _source_for_row(project, row):
    evidence = next((e for e in project.evidence if e.evidence_id == row['evidence_id']
                     and e.source_url == row['url']), None)
    return source_for_evidence(project, evidence) if evidence else next((
        s for s in project.background_agent.get('sources', []) if s.get('url') == row['url']), {})


def audit_synthesis(agent, project, proposed):
    """Check original passages one source at a time, avoiding unbounded full-text prompts."""
    reviews = []
    rows = synthesis_rows(project)
    if not rows:
        raise ValueError('缺少证据表，不能核查并发布综述。')
    for row in rows:
        source = _source_for_row(project, row)
        passages = assessment_passages(source, row['quote'])
        if not passages:
            raise ValueError('缺少报告核查所需原文：' + row['title'])
        review = grounded_review(agent, project, {
            'proposed_assessment': proposed,
            'target_source': {k: row[k] for k in ('evidence_id', 'title', 'url')},
            'actual_read_passages': [t[:12000] for t in passages],
        }, instructions='''
Check every claim attributed to target_source in the proposed report, including table cells,
effect sizes, confidence intervals, certainty levels and causal interpretations. target_source
is only a source identity. The ONLY text under review is proposed_assessment, not prior rows,
shared-context summaries, or previous drafts. Copy claim_quote from proposed_assessment itself.
Check qualifiers such as "all" and "no effect" carefully. If a source-specific detail is not
established by the supplied passages, reject it or request further reading; do not infer it
from earlier audit commentary. Other sources' claims are checked in separate source audits.''')
        reviews.append(review)
        if not review.supported:
            return review
    if len(rows) > 1:
        review = agent.call(project, SourceAssessmentReview, {
            'proposed_assessment': proposed, 'evidence_rows': rows,
        }, role='skeptical_reviewer', instructions_override=SOURCE_REVIEW_INSTRUCTIONS +
            '\nIndividual source claims have been checked separately. Now check cross-source synthesis, '
            'contradictions, scope, missing evidence and whether conclusions exceed the combined findings.')
        reviews.append(review)
        if not review.supported:
            return review
    return SourceAssessmentReview(supported=True,
        rationale='\n\n'.join(review.rationale for review in reviews))


def run_review(orchestrator, project, *, max_revisions=1):
    attempts = {}
    while True:
        try:
            return _run_review_once(orchestrator, project)
        except ValueError:
            scope = project.literature_review.get('revision_scope', 'report')
            if (attempts.get(scope, 0) >= max_revisions or
                    sum(attempts.values()) >= max_revisions * (len(project.evidence) + 1) or
                    project.literature_review.get('status') != 'needs_revision'):
                raise
            attempts[scope] = attempts.get(scope, 0) + 1
            # Saved, grounded correction feedback is input to the next attempt.
            # This never changes human approvals or turns a rejected draft into a result.
            project.literature_review['automatic_revisions'] = project.literature_review.get('automatic_revisions', 0) + 1
            orchestrator.store.save(project)


def _run_review_once(orchestrator, project):
    if project.approval_status != 'valid':
        raise ValueError('文献综合需要有效的研究方案审批。')
    if project.approval_valid_for_versions != orchestrator._review_artifact_versions(project):
        raise ValueError('研究方案已变化，请重新审批当前版本后再执行文献综合。')
    if not project.evidence:
        raise ValueError('没有已采用证据，不能生成已完成的文献综述。')
    fingerprint = hashlib.sha256(json.dumps({
        'objective': project.objective, 'constraints': project.constraints,
        'question': project.question.model_dump(mode='json') if project.question else None,
        'evidence': [e.model_dump(mode='json') for e in project.evidence],
        'design': project.study_design.model_dump(mode='json') if project.study_design else None,
        'analysis': project.analysis_plan.model_dump(mode='json') if project.analysis_plan else None,
        'source_hashes': [s.get('retrieval_snapshot', {}).get('sha256') for s in project.background_agent.get('sources', [])],
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    state = project.literature_review
    if state.get('fingerprint') != fingerprint:
        project.literature_review = state = {'fingerprint': fingerprint, 'status': 'extracting', 'rows': []}
    if state.get('status') == 'complete':
        return []
    agent = BackgroundAgent(orchestrator)
    artifacts = []
    for evidence in project.evidence:
        if any(row['evidence_id'] == evidence.evidence_id for row in state['rows']):
            continue
        state['revision_scope'] = evidence.evidence_id
        source = source_for_evidence(project, evidence)
        passages = assessment_passages(source, evidence.source_quote or '')
        if not passages:
            raise ValueError('缺少已读取的原文凭据，请返回资料阶段阅读来源，不能凭摘要完成综述：' + evidence.title)
        payload = {'question': project.objective, 'evidence_id': evidence.evidence_id,
                   'title': evidence.title, 'url': evidence.source_url,
                   'actual_read_passages': [t[:12000] for t in passages],
                   'previous_failure': state.get('extraction_errors', {}).get(evidence.evidence_id)}
        row = agent.call(project, EvidenceRow, payload, instructions_override=
            'Extract a source-specific evidence table row from ONLY the provided read passages. '
            'Separate study design, population, comparison, outcome, finding and certainty. '
            'Use unknown/not applicable honestly. Preserve negative results and distinguish hypothesis from consensus. '
            'Report no statistically significant difference as such, never as proven no effect or equivalence, '
            'even if the paper uses shorthand. Separate measured outcomes from authors\' proposed mechanisms; '
            'spatial associations or indirect proxies alone do not demonstrate the intervening behavior. '
            'Copy an exact contiguous quote. Do not upgrade certainty because a paper is a review. '
            'Write prose fields only in the language of the research question, without bilingual duplication; '
            'keep the exact quote in its original language. This is extraction, not proof of scientific truth.')
        if row.evidence_id != evidence.evidence_id or not any(normalize_text(row.quote) in normalize_text(t) for t in passages):
            state.setdefault('extraction_errors', {})[evidence.evidence_id] = {
                'draft': row.model_dump(), 'correction': 'Use the supplied evidence_id and copy one contiguous quote from actual_read_passages. The prior row was NOT saved.'}
            state['status'] = 'needs_revision'
            orchestrator.store.save(project)
            raise ValueError('证据表的来源编号或原文摘录不匹配，未保存该行。')
        audit = grounded_review(agent, project, {**payload, 'proposed_assessment': row.model_dump()})
        if not audit.supported:
            state.setdefault('extraction_errors', {})[evidence.evidence_id] = {
                'draft': row.model_dump(), 'correction': audit.model_dump()}
            state['status'] = 'needs_revision'
            orchestrator.store.save(project)
            raise ValueError('证据提取未通过原文核查：' + audit.rationale)
        saved = {**row.model_dump(), 'url': evidence.source_url, 'title': evidence.title, 'audit': audit.model_dump()}
        state['rows'].append(saved)
        state.get('extraction_errors', {}).pop(evidence.evidence_id, None)
        artifacts.append(agent.record(project, 'literature_evidence_row', saved))
    if not state.get('draft') and not state.get('extraction_errors'):
        # Legacy extraction failures were also copied to last_error. Once the
        # row is corrected, do not feed stale row-review opinions to the writer
        # as if they were an outstanding review of the whole report.
        state.pop('last_error', None)
    state['revision_scope'] = 'report'
    if state.get('status') == 'auditing' and state.get('draft'):
        # An invalid auditor response is not a request to rewrite the report.
        # Resume checking the exact saved draft until a valid review accepts it
        # or supplies grounded scientific corrections.
        return _audit_and_publish(orchestrator, project, agent,
                                  LiteratureOutput.model_validate(state['draft']), artifacts)
    state['status'] = 'synthesizing'
    orchestrator.store.save(project)
    synthesis_input = {
        'objective': project.objective, 'constraints': project.constraints, 'evidence_rows': synthesis_rows(project),
        'source_read_receipts': synthesis_passages(project),
        'design': project.study_design, 'analysis_plan': project.analysis_plan,
        'scope': project.background_agent.get('scope'), 'previous_review': state.get('last_error'),
    }
    if not state.get('readiness'):
        ready = agent.call(project, LiteratureReadiness, synthesis_input, role='scientific_synthesizer', instructions_override=
            'Check whether the approved bounded literature synthesis can be written from the available evidence. '
            'Return only readiness metadata and a concise public update, not the report. '
            'complete means ready to WRITE a draft, not that the research is verified. '
            'Use needs_input only for indispensable missing decisions/data or approved analyses that cannot be performed. '
            'Limited search and uncertainty can be reported as limitations; do not demand exhaustive review for a bounded task.')
        if ready.decision == 'needs_input':
            state['status'] = 'needs_input'
            orchestrator._queue_clarifications(project, [ready.missing_input or '请确认综述范围和缺失资料。'])
            orchestrator.store.save(project)
            return artifacts
        state['readiness'] = ready.model_dump()
        orchestrator.store.save(project)
    draft = agent.call(project, MarkdownDraft, synthesis_input, role='scientific_synthesizer', instructions_override=
        'Execute the approved literature/evidence synthesis, not a numerical experiment. '
        'Write the public report directly in Markdown. No JSON envelope or private reasoning. '
        'Write a readable report with inline source links and an evidence comparison table. '
        'Distinguish descriptive findings, causal hypotheses and intervention outcomes when relevant. '
        'Follow the actual question, not a fixed subject template. Use the evidence rows and supplied original read receipts. '
        'Explain disagreements, bias, limited search coverage and unanswered questions. '
        'Do not call a limited narrative/rapid review an exhaustive systematic review. '
        'Do not invent studies or computed meta-analysis. If the approved method requires unavailable '
        'quantitative pooling or other missing execution, clearly state it has not been performed. '
        'A non-significant result does not prove equivalence, no effect or a placebo mechanism. '
        'Do not attribute between-comparator differences mainly to a mechanism without direct evidence. '
        'Do not include self-issued audit/approval/completion certificates in the report; the server records that separately. '
        'Never invent missing table values, equate non-significance with no effect, or homogenize different certainty ratings. '
        'A value missing from an extracted row does NOT mean the paper did not report it. Inspect the original '
        'read receipts before making that claim. If unavailable in these excerpts, say not extracted/needs further reading, '
        'or omit the field; never say the study did not report it without support. '
        'Previous review comments are corrections, not new source evidence. '
        'Report completion only for the stated bounded synthesis, not discovery of scientific truth.')
    result = LiteratureOutput(**state['readiness'], report_markdown=draft.text)
    return _audit_and_publish(orchestrator, project, agent, result, artifacts)


def _audit_and_publish(orchestrator, project, agent, result, artifacts):
    state = project.literature_review
    state['draft'] = result.model_dump()
    if result.decision == 'needs_input':
        state['status'] = 'needs_input'
        orchestrator._queue_clarifications(project, [result.missing_input or '请确认综述范围和缺失资料。'])
        orchestrator.store.save(project)
        return artifacts
    state['status'] = 'auditing'
    orchestrator.store.save(project)
    audit = audit_synthesis(agent, project, result.model_dump())
    if not audit.supported:
        state.update(status='needs_revision', last_error=audit.model_dump())
        orchestrator.store.save(project)
        raise ValueError('综述结论超出证据或存在遗漏，未发布：' + audit.rationale)
    state.update(status='complete', report_markdown=result.report_markdown, audit=audit.model_dump(),
                 completed_at=utc_now().isoformat(), limitations=result.limitations, open_questions=result.open_questions)
    record = orchestrator.artifacts.save_named_text(project.project_id, 'literature_review.md',
        'literature_review', result.report_markdown, 'scientific_synthesizer')
    project.artifacts.append(record)
    artifacts.append(record.artifact_id)
    artifacts.append(agent.record(project, 'literature_execution', state))
    return artifacts
