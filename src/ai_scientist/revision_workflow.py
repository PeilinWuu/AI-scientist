"""Deterministic normalization and verification for human-approved revision cycles."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from src.ai_scientist.schemas import (
    ApprovedRevisionPlan,
    ResearchProject,
    ReviewResult,
    RevisionAction,
    RevisionCriterionResult,
    RevisionIssue,
    RevisionIssueDecision,
    RevisionTargetBatch,
)


SYSTEM_GATE_PREFIXES = ("quality gate failed:", "system quality gate:")
EXECUTION_PHRASES = (
    "actually execute",
    "actual execution",
    "conduct the meta-analysis",
    "execute quantitative",
    "perform dual independent",
    "complete dual independent",
    "register prospero",
    "registered on prospero",
    "真实执行",
    "实际执行",
    "真正执行",
    "真正注册",
    "真实注册",
    "双人筛选已完成",
)


def is_system_quality_gate(text: str) -> bool:
    normalized = text.strip().lower()
    return any(normalized.startswith(prefix) for prefix in SYSTEM_GATE_PREFIXES)


def normalize_revision_plan(review: ReviewResult, planning_only: bool) -> list[RevisionIssue]:
    """Convert reviewer actions into deduplicated, correctly routed user-facing issues."""

    actions = [action for action in review.revision_plan if not _action_is_system_gate(action)]
    if not actions:
        actions = [
            RevisionAction(
                target=review.required_revision_target if review.required_revision_target != "none" else "method",
                reason=issue,
                required_changes=[issue],
                completion_criteria=["The planning defect is explicitly resolved in the target artifact."],
            )
            for issue in review.blocking_issues
            if not is_system_quality_gate(issue)
        ]

    issues: list[RevisionIssue] = []
    for action in actions:
        text = _action_text(action)
        classification = _classify_action(text, action.priority, planning_only)
        target = _route_target(action.target, text, classification)
        issue = RevisionIssue(
            source_action_ids=[action.action_id],
            classification=classification,
            target=target,
            problem=action.reason or (action.required_changes[0] if action.required_changes else "需要修订研究计划。"),
            severity=_severity_label(classification),
            impact=_impact_text(target, classification),
            reviewer_recommendations=list(dict.fromkeys(action.required_changes)),
            completion_criteria=normalize_completion_criteria(action.completion_criteria, planning_only),
            priority=action.priority,
        )
        _merge_or_append(issues, issue)

    for text in review.non_blocking_issues:
        if is_system_quality_gate(text):
            continue
        issue = RevisionIssue(
            classification="non_blocking",
            target=_route_target("method", text, "non_blocking"),
            problem=text,
            severity="建议改进，但不阻止规划审批",
            impact="该问题影响完整性，但不应阻止 planning-only 研究计划进入人工批准。",
            reviewer_recommendations=[text],
            completion_criteria=[],
            priority=50,
        )
        _merge_or_append(issues, issue)

    return sorted(issues, key=lambda item: (_classification_order(item.classification), item.priority, item.target))


def normalize_completion_criteria(criteria: list[str], planning_only: bool) -> list[str]:
    """Keep planning reviews from requiring work that can only happen during execution."""

    if not planning_only:
        return list(dict.fromkeys(criteria))
    normalized: list[str] = []
    for criterion in criteria:
        lowered = criterion.strip().lower()
        if "imputation" in lowered and "source" in lowered:
            value = (
                "The plan states concrete imputation rules and identifies source studies or marks source "
                "validation as required before execution."
            )
        elif "mapping table" in lowered and "source" in lowered:
            value = (
                "The plan provides an explicit mapping table and identifies the authoritative guideline "
                "source that must be verified before execution."
            )
        elif "code is archived" in lowered or "code archived" in lowered:
            value = "The plan defines a version-control repository, release archive, and DOI-minting procedure."
        elif "code implements" in lowered:
            value = "The plan records the choices that later execution code must implement."
        elif "applied consistently in pilot extraction" in lowered:
            value = "The plan defines a pilot extraction procedure and a consistency check for the mapping."
        elif "pre-registered" in lowered or "preregistered" in lowered:
            value = "The plan names a registration destination and states that the rules will be fixed before analysis."
        elif "search logs are versioned and archived" in lowered:
            value = "The plan defines versioned search-log fields, archival location, dates, and result counts."
        else:
            value = criterion.strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def build_approved_revision_plan(
    project: ResearchProject,
    decisions: list[RevisionIssueDecision],
) -> ApprovedRevisionPlan:
    """Persist human dispositions and batch only issues approved for AI revision."""

    issue_by_id = {issue.issue_id: issue for issue in project.revision_issues}
    decision_by_id = {decision.issue_id: decision for decision in decisions}
    missing = sorted(set(issue_by_id) - set(decision_by_id))
    unknown = sorted(set(decision_by_id) - set(issue_by_id))
    if missing:
        raise ValueError(f"Every revision issue requires a decision. Missing: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"Unknown revision issue ids: {', '.join(unknown)}")

    approved: list[str] = []
    rejected: list[str] = []
    deferred: list[str] = []
    limitations: list[str] = []
    modified: dict[str, str] = {}
    grouped: dict[str, list[tuple[RevisionIssue, RevisionIssueDecision]]] = defaultdict(list)

    for issue_id, issue in issue_by_id.items():
        issue.completion_criteria = normalize_completion_criteria(
            issue.completion_criteria,
            project.planning_only,
        )
        decision = decision_by_id[issue_id]
        if decision.disposition in {"accept_modified", "provide_content", "reject"} and not (
            decision.instruction.strip() or decision.reason.strip()
        ):
            raise ValueError(f"Decision {decision.disposition} requires instructions or a reason for {issue_id}.")
        if decision.disposition in {"accept_ai", "accept_modified", "provide_content"}:
            approved.append(issue_id)
            issue.status = "approved"
            grouped[issue.target].append((issue, decision))
            if decision.instruction.strip():
                modified[issue_id] = decision.instruction.strip()
        elif decision.disposition == "accept_limitation":
            limitations.append(issue_id)
            issue.status = "limitation"
            project.accepted_limitations.append(decision.reason.strip() or issue.problem)
        elif decision.disposition == "defer_execution":
            deferred.append(issue_id)
            issue.status = "deferred"
            project.execution_requirements.append(decision.instruction.strip() or issue.problem)
        else:
            rejected.append(issue_id)
            issue.status = "rejected"

    batches: list[RevisionTargetBatch] = []
    for target, pairs in sorted(grouped.items(), key=lambda item: _target_order(item[0])):
        instructions: list[str] = []
        provided_content: list[str] = []
        for issue, decision in pairs:
            instruction = decision.instruction.strip()
            if instruction:
                instructions.append(instruction)
            else:
                instructions.extend(issue.reviewer_recommendations or [issue.problem])
            if decision.disposition == "provide_content" and instruction:
                provided_content.append(instruction)
        batches.append(
            RevisionTargetBatch(
                target=target,
                issue_ids=[issue.issue_id for issue, _ in pairs],
                issue_snapshots=[issue.model_copy(deep=True) for issue, _ in pairs],
                completion_criteria=list(
                    dict.fromkeys(
                        criterion
                        for issue, _ in pairs
                        for criterion in issue.completion_criteria
                    )
                ),
                instructions=list(dict.fromkeys(instructions)),
                provided_content=list(dict.fromkeys(provided_content)),
            )
        )

    return ApprovedRevisionPlan(
        project_id=project.project_id,
        review_version=len(project.reviews),
        revision_cycle=project.iteration + 1,
        approved_issues=approved,
        rejected_issues=rejected,
        deferred_issues=deferred,
        accepted_as_limitation=limitations,
        human_modified_instructions=modified,
        target_batches=batches,
    )


def deterministic_verify_batch(
    batch: RevisionTargetBatch,
    issues: list[RevisionIssue],
    artifact: Any,
) -> list[RevisionCriterionResult]:
    """Check concrete artifact content before an independent model evaluates it."""

    raw = json.dumps(artifact, ensure_ascii=False, default=str)
    lowered = raw.lower()
    results: list[RevisionCriterionResult] = []
    criteria = [criterion for issue in issues for criterion in issue.completion_criteria]
    if not criteria:
        criteria = ["The approved revision instructions are explicitly represented in the artifact."]
    for criterion in list(dict.fromkeys(criteria)):
        passed, evidence, note = _criterion_check(criterion, lowered, raw)
        results.append(
            RevisionCriterionResult(
                criterion=criterion,
                passed=passed,
                evidence=evidence,
                note=note,
            )
        )
    return results


def combine_revision_verification_results(
    deterministic: list[RevisionCriterionResult],
    independent: list[RevisionCriterionResult],
) -> list[RevisionCriterionResult]:
    """Combine hard checks with semantic review without turning advisory tokens into vetoes."""

    independent_by_criterion = {item.criterion: item for item in independent}
    combined: list[RevisionCriterionResult] = []
    for item in deterministic:
        semantic = independent_by_criterion.get(item.criterion)
        deterministic_is_advisory = item.note.startswith("Advisory semantic token check")
        combined.append(
            item.model_copy(
                update={
                    "passed": bool(semantic and semantic.passed) and (
                        item.passed or deterministic_is_advisory
                    ),
                    "evidence": semantic.evidence if semantic else item.evidence,
                    "note": (
                        f"Deterministic: {item.note} Independent: "
                        f"{semantic.note if semantic else 'criterion not assessed'}"
                    ),
                }
            )
        )
    return combined


def _criterion_check(criterion: str, lowered: str, raw: str) -> tuple[bool, str, str]:
    text = criterion.lower()
    groups: list[list[str]] = []
    if any(token in text for token in ["search string", "boolean", "executable"]):
        groups = [[" and ", " or "], ["pubmed", "cochrane", "pmc"]]
    elif any(token in text for token in ["filter", "date range", "field tag"]):
        groups = [["filter", "date", "year"], ["tag", "mesh", "title/abstract"]]
    elif any(token in text for token in ["search log", "result count", "versioned"]):
        groups = [["log", "archive", "version"], ["date", "count", "result"]]
    elif any(token in text for token in ["imputation", "correlation coefficient", "fallback"]):
        groups = [["imput", "missing sd"], ["correlation", "coefficient"], ["fallback", "sensitivity"]]
    elif any(token in text for token in ["mapping table", "intensity", "non-standard metric"]):
        groups = [["hrmax"], ["hrr"], ["met"], ["rpe"], ["light"], ["moderate"], ["vigorous"]]
    elif any(token in text for token in ["estimator", "correction"]):
        groups = [["reml", "derSimonian".lower(), "maximum likelihood"], ["correction", "zero-cell", "continuity"]]
    elif "sensitivity" in text:
        groups = [["sensitivity", "robustness"]]
    elif any(token in text for token in ["overlap", "citation matrix", "deduplication"]):
        groups = [["overlap", "citation matrix"], ["deduplic", "double count"]]
    elif any(token in text for token in ["environment", "seed", "software"]):
        groups = [["version", "environment"], ["seed"], ["r ", "stata", "python", "revman"]]
    elif any(token in text for token in ["functional form", "linear", "quadratic"]):
        groups = [["linear"], ["quadratic", "spline", "nonlinear"]]
    elif "covariate" in text:
        groups = [["baseline"], ["medication"], ["weight", "bmi"]]
    elif any(token in text for token in ["registration destination", "fixed before analysis"]):
        groups = [["prospero", "osf", "registration"], ["before analysis", "a priori", "pre-specif"]]
    elif any(token in text for token in ["pilot extraction", "consistency check"]):
        groups = [["pilot"], ["consisten", "agreement", "calibration"]]
    elif "later execution code" in text:
        groups = [["implement", "execution code", "analysis code"], ["pre-specif", "fixed", "a priori"]]
    else:
        words = [word for word in re.findall(r"[a-z0-9%]+", text) if len(word) > 4]
        hits = [word for word in words if word in lowered]
        passed = len(hits) >= min(2, max(1, len(words)))
        return passed, ", ".join(hits[:8]), "Advisory semantic token check; independent verification is authoritative."

    missing = [group for group in groups if not any(token.lower() in lowered for token in group)]
    passed = not missing
    found = [next((token for token in group if token.lower() in lowered), "") for group in groups]
    return passed, ", ".join(item for item in found if item)[:500], (
        "Concrete required elements found." if passed else f"Missing semantic groups: {missing}"
    )


def _action_is_system_gate(action: RevisionAction) -> bool:
    if action.reason.strip().lower().startswith("quality review requires revision of"):
        return True
    return all(
        is_system_quality_gate(text)
        for text in [action.reason, *action.required_changes]
        if text.strip()
    )


def _action_text(action: RevisionAction) -> str:
    return " ".join([action.reason, *action.required_changes, *action.completion_criteria]).lower()


def _classify_action(text: str, priority: int, planning_only: bool) -> str:
    if planning_only and any(phrase in text for phrase in EXECUTION_PHRASES) and not any(
        token in text for token in ["specify", "define", "pre-specify", "document", "rule", "strategy", "plan"]
    ):
        return "execution_prerequisite"
    if priority >= 4:
        return "optional"
    if priority >= 3 or any(token in text for token in ["language policy", "translation", "clinical review"]):
        return "non_blocking"
    return "plan_blocking"


def _route_target(original: str, text: str, classification: str) -> str:
    if classification == "execution_prerequisite":
        return "execution_requirements"
    if any(token in text for token in ["random-effects", "estimator", "continuity correction", "missing sd", "imputation", "correlation coefficient", "dose–response", "dose-response", "meta-regression", "hrmax", "hrr", "mets", "rpe", "intensity harmon"]):
        return "analysis_plan"
    if any(token in text for token in ["boolean", "database-specific", "search strateg", "screening protocol", "search log", "propero", "prospero", "software environment", "package version", "random seed", "citation matrix", "overlap"]):
        return "reproducibility_plan"
    if any(token in text for token in ["unsupported hypoth", "exploratory hypoth", "hypothesis-generating"]) or (
        "hypoth" in text and "unsupported" in text
    ):
        return "hypothesis"
    return {
        "method": "methodology",
        "analysis": "analysis_plan",
        "design": "study_design",
        "reproducibility": "reproducibility_plan",
    }.get(original, original if original in {
        "question", "evidence", "hypothesis", "methodology", "study_design", "analysis_plan", "reproducibility_plan"
    } else "methodology")


def _merge_or_append(issues: list[RevisionIssue], incoming: RevisionIssue) -> None:
    incoming_tokens = _signature_tokens(incoming.problem + " " + " ".join(incoming.reviewer_recommendations))
    for index, existing in enumerate(issues):
        if existing.target != incoming.target or existing.classification != incoming.classification:
            continue
        existing_tokens = _signature_tokens(existing.problem + " " + " ".join(existing.reviewer_recommendations))
        union = existing_tokens | incoming_tokens
        similarity = len(existing_tokens & incoming_tokens) / len(union) if union else 0
        thematic_duplicate = (
            {"language", "translation"} & existing_tokens
            and {"language", "translation"} & incoming_tokens
        ) or ("hypotheses" in existing_tokens and "hypotheses" in incoming_tokens and "unsupported" in (existing_tokens | incoming_tokens))
        if similarity >= 0.55 or thematic_duplicate:
            issues[index] = existing.model_copy(
                update={
                    "source_action_ids": list(dict.fromkeys(existing.source_action_ids + incoming.source_action_ids)),
                    "reviewer_recommendations": list(dict.fromkeys(existing.reviewer_recommendations + incoming.reviewer_recommendations)),
                    "completion_criteria": list(dict.fromkeys(existing.completion_criteria + incoming.completion_criteria)),
                    "priority": min(existing.priority, incoming.priority),
                }
            )
            return
    issues.append(incoming)


def _signature_tokens(text: str) -> set[str]:
    stop = {"the", "and", "for", "with", "must", "are", "not", "plan", "research", "document"}
    return {token for token in re.findall(r"[a-z0-9%]+", text.lower()) if len(token) > 3 and token not in stop}


def _classification_order(value: str) -> int:
    return {"plan_blocking": 0, "execution_prerequisite": 1, "non_blocking": 2, "optional": 3}[value]


def _target_order(value: str) -> int:
    order = ["question", "evidence", "hypothesis", "methodology", "study_design", "analysis_plan", "reproducibility_plan", "execution_requirements"]
    return order.index(value) if value in order else len(order)


def _severity_label(classification: str) -> str:
    return {
        "plan_blocking": "需要在方案定稿前处理",
        "execution_prerequisite": "需要在实际执行阶段完成",
        "non_blocking": "建议改进，但不阻止规划审批",
        "optional": "可选优化",
    }[classification]


def _impact_text(target: str, classification: str) -> str:
    if classification == "execution_prerequisite":
        return "这是未来执行要求；planning-only 阶段只需明确执行安排，不要求现在已经完成。"
    return {
        "analysis_plan": "若未预先规定，后续分析可能产生事后方法选择偏倚。",
        "reproducibility_plan": "若缺少可执行细节，其他研究者无法复现计划中的流程。",
        "hypothesis": "若不修订，假设可能超出当前证据能够支持的范围。",
        "methodology": "若不修订，研究方法的适用边界和控制策略仍不明确。",
    }.get(target, "若不处理，研究计划的可执行性或科学有效性可能下降。")
