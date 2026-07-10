"""Citation metadata helpers."""


def explicit_sources_only(sources: list[dict]) -> list[dict]:
    """Keep only provider-returned sources with explicit URLs."""

    return [source for source in sources if source.get("url")]
