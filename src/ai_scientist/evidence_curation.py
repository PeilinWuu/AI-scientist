"""Deterministic project binding and lightweight source curation helpers."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from urllib.parse import urlparse

from src.ai_scientist.exceptions import StaleSearchPlanError
from src.ai_scientist.schemas import (
    CuratedSource,
    ResearchProject,
    SearchPlan,
    SourceCandidate,
    SourceReviewFeedbackSummary,
)
from src.ai_scientist.source_selector import source_score


CONCEPT_GROUPS = {
    "hypertension": {"hypertension", "high blood pressure", "高血压"},
    "blood pressure": {"blood pressure", "sbp", "dbp", "收缩压", "舒张压", "血压"},
    "aerobic exercise": {
        "aerobic exercise", "aerobic training", "walking", "cycling", "有氧运动", "有氧训练", "步行", "骑行",
    },
    "retrieval augmented generation": {"retrieval augmented generation", "rag", "检索增强生成"},
    "hybrid search": {"hybrid search", "混合检索"},
}
STOP_WORDS = {
    "the", "and", "for", "with", "from", "into", "does", "what", "how", "研究", "影响", "系统", "评估",
    "是否", "能够", "不同", "当前", "成人", "成年人", "比较", "作用",
}


def research_question_version(project: ResearchProject) -> int:
    version = project.active_artifact_versions.get("research_question")
    return int(version or 1)


def compute_question_hash(project: ResearchProject) -> str:
    if project.question is None:
        raise ValueError("A formulated research question is required.")
    material = "\n".join(
        [project.question.normalized_question.strip(), project.question.scope.strip(), project.domain.strip()]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def bind_search_plan(project: ResearchProject, plan: SearchPlan, planner_model: str) -> SearchPlan:
    """Overwrite model-supplied identity fields with authoritative project data."""

    previous_versions = [item.version for item in project.search_plan_history]
    return plan.model_copy(
        update={
            "project_id": project.project_id,
            "research_question_version": research_question_version(project),
            "question_hash": compute_question_hash(project),
            "version": max(previous_versions, default=0) + 1,
            "planner_model": planner_model,
            "approved_at": None,
            "approved_by": None,
        }
    )


def validate_search_plan_binding(project: ResearchProject, plan: SearchPlan) -> None:
    current_hash = compute_question_hash(project)
    if plan.project_id != project.project_id or plan.question_hash != current_hash:
        raise StaleSearchPlanError(
            "Search plan does not match the active project and research question.",
            stage="SEARCH_PLAN_REVIEW",
            substep="search_plan_binding",
            cause_type="StaleSearchPlanError",
            cause_message="project_id or question_hash mismatch",
        )


def validate_checkpoint_binding(project: ResearchProject) -> None:
    checkpoint = project.background_research_checkpoint
    if not checkpoint.search_plan:
        return
    if (
        checkpoint.project_id != project.project_id
        or checkpoint.question_hash != compute_question_hash(project)
        or checkpoint.search_plan_id != checkpoint.search_plan.search_plan_id
    ):
        raise StaleSearchPlanError(
            "Search checkpoint does not match the active project and research question.",
            stage="BACKGROUND_RESEARCH",
            substep="search_checkpoint_binding",
            cause_type="StaleSearchPlanError",
            cause_message="checkpoint project/question/search-plan binding mismatch",
        )


def deterministic_plan_relevance(project: ResearchProject, plan: SearchPlan) -> tuple[str, str]:
    """Reject obvious topic drift before any network call."""

    question = " ".join(
        [project.question.normalized_question if project.question else project.objective,
         project.question.scope if project.question else ""]
    ).lower()
    query_text = " ".join(plan.queries).lower()
    concepts = _question_concepts(question)
    matched = [concept for concept, aliases in concepts.items() if any(alias in query_text for alias in aliases)]
    if concepts and not matched:
        return "irrelevant", f"检索式未覆盖研究问题核心概念：{', '.join(concepts)}。"
    if len(matched) < max(1, len(concepts) // 2):
        return "partially_relevant", f"仅覆盖部分核心概念：{', '.join(matched)}。"
    return "relevant", f"检索式覆盖核心概念：{', '.join(matched)}。"


def enrich_candidates(project: ResearchProject, candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    """Add review-oriented metadata without downloading full text."""

    question_text = " ".join(
        [project.question.normalized_question if project.question else project.objective,
         project.question.scope if project.question else ""]
    ).lower()
    concepts = _question_concepts(question_text)
    enriched: list[SourceCandidate] = []
    for candidate in candidates:
        candidate_text = " ".join([candidate.title, candidate.snippet, candidate.query]).lower()
        covered = sum(any(alias in candidate_text for alias in aliases) for aliases in concepts.values())
        relevance = covered / len(concepts) if concepts else _token_overlap(question_text, candidate_text)
        formal_score = source_score(candidate)
        verified = "verified" if candidate.doi or candidate.pmid or formal_score >= 90 else "partially_verified" if candidate.url else "unverified"
        if relevance >= 0.5 and formal_score >= 80:
            recommendation = "keep"
            reason = "主题与研究问题直接相关，且来源具备较强的正式出版或数据库信号。"
        elif relevance == 0:
            recommendation = "reject"
            reason = "标题、摘要和检索式未覆盖当前研究问题的核心概念。"
        else:
            recommendation = "uncertain"
            reason = "可能相关，但需要研究者核对研究对象、干预、结局或来源质量。"
        year = candidate.publication_year or _extract_year(candidate.snippet)
        enriched.append(
            candidate.model_copy(
                update={
                    "source_domain": candidate.source_domain or urlparse(candidate.url).netloc.lower(),
                    "publication_year": year,
                    "relevance_score": round(relevance, 3),
                    "verification_status": verified,
                    "verification_note": "候选阶段仅验证标识符和来源域，正式验证在人工筛选后进行。",
                    "ai_summary": candidate.snippet or "搜索接口未返回摘要，请打开来源核对。",
                    "ai_recommendation": recommendation,
                    "recommendation_reason": reason,
                    "selection_score": formal_score,
                }
            )
        )
    return sorted(enriched, key=_candidate_sort_key)


def parse_human_source(entry: str, rank: int) -> SourceCandidate:
    value = entry.strip()
    doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", value, flags=re.IGNORECASE)
    arxiv_match = re.search(
        r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)?((?:[a-z-]+(?:\.[A-Z]{2})?/)?\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?",
        value,
        flags=re.IGNORECASE,
    )
    explicit_pmid_match = re.search(r"PMID\s*:\s*(\d{4,12})", value, flags=re.IGNORECASE)
    bare_pmid_match = re.fullmatch(r"\d{4,12}", value)
    is_url = value.startswith(("http://", "https://"))
    doi = doi_match.group(0).rstrip(".,;").lower() if doi_match else None
    arxiv_id = arxiv_match.group(1) if arxiv_match and not doi else None
    pmid_match = explicit_pmid_match or bare_pmid_match
    pmid = pmid_match.group(1) if pmid_match and not doi and not arxiv_id else None
    if doi:
        url = f"https://doi.org/{doi}"
        title = f"DOI: {doi}"
    elif pmid:
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        title = f"PMID: {pmid}"
    elif arxiv_id:
        url = f"https://arxiv.org/abs/{arxiv_id}"
        title = f"arXiv: {arxiv_id}"
    elif is_url:
        url, title = value, value
    else:
        url, title = "", value
    return SourceCandidate(
        title=title,
        url=url,
        query="human_provided_source",
        rank=rank,
        source_domain=urlparse(url).netloc.lower() if url else "",
        doi=doi,
        pmid=pmid,
        arxiv_id=arxiv_id,
        human_provided=True,
        ai_recommendation="uncertain",
        recommendation_reason="该来源由研究者提供，系统将在保留后进行正式验证。",
        ai_summary="人工提供的来源，当前尚未提取或分析内容。",
    )


def summarize_source_feedback(decisions: list[CuratedSource]) -> SourceReviewFeedbackSummary:
    rejected = [item for item in decisions if item.decision == "reject"]
    counts = Counter(item.rejection_reason or "其他" for item in rejected)
    return SourceReviewFeedbackSummary(
        rejection_reason_counts=dict(counts),
        concise_feedback=[f"{reason}: {count}" for reason, count in counts.most_common()],
    )


def _question_concepts(text: str) -> dict[str, set[str]]:
    concepts = {
        name: aliases for name, aliases in CONCEPT_GROUPS.items() if any(alias in text for alias in aliases)
    }
    tokens = {
        token for token in re.findall(r"[a-z][a-z0-9-]{3,}", text)
        if token not in STOP_WORDS
    }
    for token in sorted(tokens)[:8]:
        if not any(token in aliases for aliases in concepts.values()):
            concepts[token] = {token}
    return concepts


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[\w-]{2,}", left)) - STOP_WORDS
    right_tokens = set(re.findall(r"[\w-]{2,}", right)) - STOP_WORDS
    return len(left_tokens & right_tokens) / max(1, len(left_tokens))


def _extract_year(value: str) -> str | None:
    match = re.search(r"\b(?:19|20)\d{2}\b", value or "")
    return match.group(0) if match else None


def _candidate_sort_key(item: SourceCandidate) -> tuple[int, int, float, int]:
    verified_rank = {"verified": 0, "partially_verified": 1, "unverified": 2, "contradicted": 3, "invalid": 4}
    recommendation_rank = {"keep": 0, "uncertain": 1, "reject": 2}
    return (
        recommendation_rank[item.ai_recommendation],
        verified_rank[item.verification_status],
        -item.relevance_score,
        -item.selection_score,
    )
