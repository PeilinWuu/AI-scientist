"""Deterministic selection of high-value search candidates."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from src.ai_scientist.evidence_verifier import normalize_title
from src.ai_scientist.schemas import SearchCandidate


HIGH_PRIORITY_HOSTS = {
    "pubmed.ncbi.nlm.nih.gov": 100,
    "pmc.ncbi.nlm.nih.gov": 98,
    "cochranelibrary.com": 96,
    "doi.org": 92,
    "who.int": 88,
    "nih.gov": 88,
    "cdc.gov": 88,
    "gov": 84,
}
PUBLISHER_TOKENS = {
    "nature.com",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "bmj.com",
    "jamanetwork.com",
    "nejm.org",
    "oup.com",
    "tandfonline.com",
    "sagepub.com",
    "plos.org",
    "frontiersin.org",
}
LOW_PRIORITY_TOKENS = {"blog", "news", "medium.com", "wikipedia.org", "reddit.com", "substack.com"}


def select_sources(candidates: list[SearchCandidate], maximum: int) -> list[SearchCandidate]:
    """Deduplicate and rank candidates without asking an LLM."""

    deduplicated: list[SearchCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        keys = _candidate_keys(candidate)
        if any(key in seen for key in keys):
            continue
        seen.update(keys)
        deduplicated.append(
            candidate.model_copy(update={"url": canonicalize_url(candidate.url), "selection_score": source_score(candidate)})
        )
    return sorted(deduplicated, key=lambda item: (-item.selection_score, item.rank, item.title.lower()))[:maximum]


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=False)
            if not key.lower().startswith("utm_") and key.lower() not in {"ref", "source", "campaign"}
        )
    )
    return urlunparse((parsed.scheme.lower() or "https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", query, ""))


def source_score(candidate: SearchCandidate) -> int:
    host = urlparse(candidate.url).netloc.lower().removeprefix("www.")
    score = 40
    for token, value in HIGH_PRIORITY_HOSTS.items():
        if host == token or host.endswith(f".{token}") or token in host:
            score = max(score, value)
    if any(token in host for token in PUBLISHER_TOKENS):
        score = max(score, 90)
    if candidate.doi:
        score += 8
    if candidate.pmid:
        score += 8
    if any(token in host for token in LOW_PRIORITY_TOKENS):
        score -= 35
    return score


def _candidate_keys(candidate: SearchCandidate) -> set[str]:
    keys = {f"url:{canonicalize_url(candidate.url)}"}
    doi = candidate.doi or _extract_doi(candidate.url)
    pmid = candidate.pmid or _extract_pmid(candidate.url)
    title = normalize_title(candidate.title)
    if doi:
        keys.add(f"doi:{doi.lower()}")
    if pmid:
        keys.add(f"pmid:{pmid}")
    if title:
        keys.add(f"title:{title}")
    return keys


def _extract_doi(value: str) -> str | None:
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", value, flags=re.IGNORECASE)
    return match.group(0).rstrip(".,;").lower() if match else None


def _extract_pmid(value: str) -> str | None:
    match = re.search(r"pubmed(?:\.ncbi\.nlm\.nih\.gov)?/(\d{4,12})", value, flags=re.IGNORECASE)
    return match.group(1) if match else None
