"""Deterministic evidence verification and deduplication utilities."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

from src.ai_scientist.schemas import EvidenceCollection, EvidenceItem, VerificationMethod, VerificationStatus


def verify_evidence_item(item: EvidenceItem, search_client: object | None = None) -> EvidenceItem:
    """Verify one evidence record without fabricating missing identifiers."""

    doi = _normalize_doi(item.doi or item.citation or item.source_url or "")
    pmid = _normalize_numeric_id(item.pmid or "")
    arxiv_id = _normalize_arxiv_id(item.arxiv_id or item.source_url or "")
    official_record_url = item.official_record_url or _official_record_from_url(item.source_url)
    method: VerificationMethod = "none"
    status: VerificationStatus = "unverified"
    note = item.verification_note or ""

    if doi:
        method = "doi"
        status = "verified"
        note = "DOI is present and normalized; source can be independently checked."
    elif pmid:
        method = "pmid"
        status = "verified"
        note = "PMID is present; source can be independently checked."
    elif arxiv_id:
        method = "arxiv"
        status = "verified"
        note = "arXiv identifier is present; source can be independently checked."
    elif official_record_url:
        method = "official_url"
        status = "verified"
        note = "Official or publisher-like URL is present."
    elif item.source_url:
        method = "publisher_record" if _looks_like_publisher_url(item.source_url) else "exact_title_match"
        status = "partially_verified"
        note = "URL is present but no DOI, PMID, arXiv ID, or official database record was provided."
    elif item.title and item.authors and item.publication_year:
        method = "title_author_year_match"
        status = "partially_verified"
        note = "Title, author, and year are present, but no formal identifier was provided."
    else:
        note = note or "No DOI, PMID, arXiv ID, official URL, source URL, or title-author-year match was available."

    verified = status == "verified"
    return item.model_copy(
        update={
            "doi": doi or None,
            "pmid": pmid or None,
            "arxiv_id": arxiv_id or None,
            "official_record_url": official_record_url,
            "verification_status": status,
            "verification_method": method,
            "verification_note": note,
            "verified": verified,
        }
    )


def verify_evidence_collection(
    collection: EvidenceCollection,
    search_client: object | None = None,
) -> EvidenceCollection:
    """Verify and deduplicate all evidence records in a collection."""

    verified = [verify_evidence_item(item, search_client) for item in collection.evidence_items]
    return collection.model_copy(update={"evidence_items": deduplicate_evidence(verified)})


def deduplicate_evidence(evidence: list[EvidenceItem]) -> list[EvidenceItem]:
    """Mark duplicate evidence records by stable bibliographic keys."""

    seen: dict[str, str] = {}
    result: list[EvidenceItem] = []
    for item in evidence:
        key = evidence_dedupe_key(item)
        duplicate_of = seen.get(key)
        if duplicate_of:
            result.append(item.model_copy(update={"duplicate_of": duplicate_of}))
            continue
        seen[key] = item.evidence_id
        result.append(item.model_copy(update={"duplicate_of": None}))
    return result


def evidence_dedupe_key(item: EvidenceItem) -> str:
    doi = _normalize_doi(item.doi or item.citation or item.source_url or "")
    if doi:
        return f"doi:{doi}"
    if item.pmid:
        return f"pmid:{_normalize_numeric_id(item.pmid)}"
    arxiv_id = _normalize_arxiv_id(item.arxiv_id or item.source_url or "")
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    canonical_url = _canonical_url(item.official_record_url or item.source_url)
    if canonical_url:
        return f"url:{canonical_url}"
    title = normalize_title(item.title)
    if item.authors and item.publication_year:
        return f"title-author-year:{title}:{item.authors[0].strip().lower()}:{item.publication_year}"
    return f"title:{title}"


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_doi(value: str) -> str:
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", value or "", flags=re.IGNORECASE)
    return match.group(0).rstrip(".,;").lower() if match else ""


def _normalize_numeric_id(value: str) -> str:
    match = re.search(r"\d{4,12}", value or "")
    return match.group(0) if match else ""


def _normalize_arxiv_id(value: str) -> str:
    match = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", value or "", flags=re.IGNORECASE)
    if match:
        return match.group(1)
    legacy = re.search(r"([a-z-]+/\d{7})(v\d+)?", value or "", flags=re.IGNORECASE)
    return legacy.group(1).lower() if legacy else ""


def _canonical_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value.strip().lower())
    if not parsed.netloc:
        return ""
    return f"{parsed.netloc}{parsed.path}".rstrip("/")


def _official_record_from_url(value: str | None) -> str | None:
    if not value:
        return None
    host = urlparse(value).netloc.lower()
    official_hosts = ["doi.org", "pubmed.ncbi.nlm.nih.gov", "arxiv.org", "clinicaltrials.gov"]
    if any(token in host for token in official_hosts) or _looks_like_publisher_url(value):
        return value
    return None


def _looks_like_publisher_url(value: str | None) -> bool:
    if not value:
        return False
    host = urlparse(value).netloc.lower()
    publisher_tokens = [
        "springer",
        "sciencedirect",
        "wiley",
        "nature",
        "cell.com",
        "jamanetwork",
        "nejm",
        "bmj",
        "oup",
        "tandfonline",
        "sagepub",
        "frontiersin",
        "plos",
        "mdpi",
    ]
    return any(token in host for token in publisher_tokens)
