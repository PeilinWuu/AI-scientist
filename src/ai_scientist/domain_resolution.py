"""Domain aliasing and safe domain skill resolution for AI Scientist."""

from __future__ import annotations

from src.ai_scientist.schemas import DomainResolution


REGISTERED_DOMAIN_SKILLS = {
    "general",
    "biology",
    "chemistry",
    "medicine",
    "physics",
    "computer_science",
    "social_science",
    "engineering",
    "fluid_dynamics",
}

DOMAIN_ALIASES = {
    "molecular_biology": "biology",
    "cell_biology": "biology",
    "genetics": "biology",
    "evolutionary_biology": "biology",
    "origin_of_life": "biology",
    "astrobiology": "biology",
    "biochemistry": "chemistry",
    "chemical_biology": "chemistry",
    "organic_chemistry": "chemistry",
    "inorganic_chemistry": "chemistry",
    "machine_learning": "computer_science",
    "artificial_intelligence": "computer_science",
    "organizational_science": "social_science",
    "economics": "social_science",
    "materials_science": "engineering",
    "mathematics": "general",
}


def normalize_domain_name(domain: str | None) -> str:
    """Normalize model-produced domain labels before lookup."""

    value = (domain or "general").strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in value:
        value = value.replace("__", "_")
    return value or "general"


def canonicalize_domain(domain: str | None) -> str:
    """Map fine-grained model domains to loadable domain skill names."""

    normalized = normalize_domain_name(domain)
    if normalized in REGISTERED_DOMAIN_SKILLS:
        return normalized
    if normalized in DOMAIN_ALIASES:
        return DOMAIN_ALIASES[normalized]
    return "general"


def resolve_domain(
    reported_primary_domain: str | None,
    reported_secondary_domains: list[str] | None = None,
) -> DomainResolution:
    """Return both reported and canonical domain routing metadata."""

    reported_primary = normalize_domain_name(reported_primary_domain)
    reported_secondary = [normalize_domain_name(item) for item in (reported_secondary_domains or [])]
    canonical_primary = canonicalize_domain(reported_primary)
    canonical_secondary = []
    for item in reported_secondary:
        canonical = canonicalize_domain(item)
        if canonical not in canonical_secondary:
            canonical_secondary.append(canonical)

    fallback_used = canonical_primary == "general" and reported_primary not in REGISTERED_DOMAIN_SKILLS
    if reported_primary in DOMAIN_ALIASES:
        reason = f"Mapped fine-grained domain '{reported_primary}' to '{canonical_primary}'."
    elif reported_primary in REGISTERED_DOMAIN_SKILLS:
        reason = f"Domain '{reported_primary}' is a registered skill."
    else:
        reason = f"Domain '{reported_primary}' is not registered; using general research rules."

    return DomainResolution(
        reported_primary_domain=reported_primary,
        reported_secondary_domains=reported_secondary,
        canonical_primary_domain=canonical_primary,
        canonical_secondary_domains=canonical_secondary,
        loaded_domain_skill=canonical_primary,
        fallback_used=fallback_used,
        mapping_reason=reason,
    )
