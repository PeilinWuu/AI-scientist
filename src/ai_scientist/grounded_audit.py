"""Audit corrections need provenance too; reviewers are not privileged evidence."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from src.ai_scientist.background_agent import SourceAssessmentReview, SOURCE_REVIEW_INSTRUCTIONS
from src.ai_scientist.tools.research_web import normalize_text


class AuditProtocolError(ValueError):
    stage_substep = 'audit_protocol'
    failure_category = 'review_unavailable'


def quote_text(text):
    # Normalize typography only; keep digits, signs and mathematical operators.
    return normalize_text(unicodedata.normalize('NFKC', text).translate(
        str.maketrans({'\u00ad': '', '’': "'", '‘': "'", '“': '"', '”': '"'})))


def strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


def numbers(text):
    result = set()
    # HTML/PDF extraction often separates the mathematical minus from digits
    # ("− 0.24"). Preserve its sign instead of reading it as positive 0.24.
    text = re.sub(r'−\s*(?=\d)', '-', text)
    for n in re.findall(r'(?<!\w)[−+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?', text):
        try:
            result.add(Decimal(n.replace(',', '').replace('−', '-')))
        except InvalidOperation:
            pass
    return result


def audit_errors(review, proposed, passages):
    if review.supported:
        return ['supported=true conflicts with mandatory issues/corrections'] if review.issues or review.corrections else []
    if not review.issues:
        return ['Rejection requires concrete issues with exact claim_quote and source_quote, not an unsupported reviewer opinion.']
    claims = [quote_text(s) for s in strings(proposed)]
    sources = [quote_text(s) for s in passages]
    errors = []
    for i, issue in enumerate(review.issues):
        if not issue.claim_quote.strip() or not any(quote_text(issue.claim_quote) in s for s in claims):
            errors.append(f'Issue {i+1}: claim_quote is not in the proposed text.')
        quotes = [issue.source_quote, *issue.supporting_quotes]
        for quote in quotes:
            if len(quote_text(quote)) < 12 or not any(quote_text(quote) in s for s in sources):
                errors.append(f'Issue {i+1}: source_quote/supporting_quotes must be real contiguous original passages, at least 12 characters each.')
        # A short quote is a locator, not the entire evidence context. Requiring
        # every comparison/threshold to occur in that one quote caused genuine
        # corrections to fail. This is only a numeric provenance sanity check;
        # semantic attribution is still the reviewer's job, not proved here.
        missing = numbers(issue.reason + ' ' + issue.suggested_text) - numbers(issue.claim_quote) - numbers(' '.join(passages))
        if missing:
            errors.append(f'Issue {i+1}: numbers {sorted(map(str, missing))} are absent from the actual read evidence. Recheck or omit these numbers. Never guess a correction.')
    return errors


def grounded_review(agent, project, payload, *, instructions='', max_attempts=2):
    # Immutable excerpt IDs avoid asking a model to retype long quotations.
    # Successful lookup proves provenance only, not semantic support.
    passages = payload['actual_read_passages']
    catalog = {}
    for passage in passages:
        for offset in range(0, len(passage), 1000):
            fragment = passage[offset:offset + 1000]
            if len(quote_text(fragment)) >= 12:
                catalog[f'P{len(catalog) + 1}'] = fragment
    model_payload = {**payload, 'actual_read_passages': [
        {'passage_id': key, 'text': text} for key, text in catalog.items()]}
    rules = SOURCE_REVIEW_INSTRUCTIONS + '''
The reviewer can be wrong. For every mandatory rejection, provide an issue containing:
claim_quote copied exactly from proposed_assessment, source_passage_id selected from
actual_read_passages. Use supporting_passage_ids for additional passages. The server copies
the original text; leave source_quote and supporting_quotes empty when selecting IDs.
Never invent an ID. Explain how the selected passage supports the issue; an ID match alone
is not semantic support. proposed_assessment is the ONLY audit target; never quote
a different draft, source row or shared-context summary as if it were the current proposal.
Also provide reason and optional suggested_text (a replacement, not
instructions). Use supporting_quotes for additional exact contiguous passages when a correction
depends on multiple results. Numeric values must be present in actual_read_passages; cite supporting_quotes
where practical. Methodological comments are not extra experimental evidence;
do not add numbered labels inside those fields. For missing
context quote the closest relevant supplied passage and explain the gap; never guess absent
text or numerical corrections. A fragment missing a result is not evidence of no result.
Do not reject for optional style preferences. supported=true requires no mandatory issues or
corrections. If evidence does not settle a question, ask to qualify the claim, not manufacture certainty.
''' + instructions
    errors = []
    previous_draft = None
    for _ in range(max_attempts):
        review = agent.call(project, SourceAssessmentReview,
            {**model_payload, 'audit_validation_errors': errors,
             'previous_invalid_audit_not_evidence': previous_draft},
            role='skeptical_reviewer', instructions_override=rules)
        for issue in review.issues:
            if issue.source_passage_id:
                issue.source_quote = catalog.get(issue.source_passage_id, '')
            if issue.supporting_passage_ids:
                issue.supporting_quotes = [catalog.get(key, '') for key in issue.supporting_passage_ids]
        errors = audit_errors(review, payload['proposed_assessment'], passages)
        if errors and not review.supported and review.issues:
            valid_issues = [issue for issue in review.issues if not audit_errors(
                SourceAssessmentReview(supported=False, rationale='', issues=[issue]),
                payload['proposed_assessment'], payload['actual_read_passages'])]
            if valid_issues:
                # One unsupported suggestion must not discard other concrete
                # reasons to reject a draft. The raw audit remains in receipts;
                # only validated issues become corrective instructions.
                review.issues = valid_issues
                errors = []
        if not errors:
            # Only grounded individual issues can become corrective instructions.
            if not review.supported:
                review.corrections = [f'{i.reason}\n待核对原句：{i.claim_quote}\n原文：{i.source_quote}\n补充原文：' + '\n'.join(i.supporting_quotes) + f'\n建议：{i.suggested_text}' for i in review.issues]
                # The free-form overview has not passed issue-level provenance
                # checks. Keep it in the original call receipt, but do not feed
                # it back as an additional, privileged corrective instruction.
                review.rationale = '\n'.join(i.reason for i in review.issues)
            return review
        previous_draft = review.model_dump()
    raise AuditProtocolError('审核意见自身未通过原文校验，未用于改写结论；需重新核查。' + ' '.join(errors))
