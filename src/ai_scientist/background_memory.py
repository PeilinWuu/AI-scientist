"""Immutable, bounded working context; archival source bodies are never prompts."""
from copy import deepcopy
from urllib.parse import urlsplit


def compact_result(value):
    if isinstance(value, dict):
        return {k: compact_result(v) for k, v in value.items()
                if k not in {'extracted_text', 'retrieval_snapshot', 'shown_excerpts'}}
    if isinstance(value, list):
        return [compact_result(v) for v in value[:20]]
    if isinstance(value, str):
        return value if len(value) <= 1800 else value[:1800] + '\n[Excerpt truncated; use read_source to inspect further.]'
    return deepcopy(value)


def observation_context(state):
    """Keep a stable historical index and recent results, not nested body caches."""
    observations = state.get('observations', [])
    start = max(0, len(observations) - 300)
    index = [{'id': o['id'], 'observation_alias': f'O{i+1}', 'action': o['action'], 'status': o['status']}
             for i, o in enumerate(observations[start:], start=start)]
    recent = [compact_result(o) for o in observations[-8:]]
    return index, recent


def reading_context(state):
    """Recover actual shown passages for recent sources, even after failed retries.

    These are existing read receipts, not a new claim of reading cached full text.
    Full originals remain available through read_source(offset).
    """
    reads = {}
    for o in reversed(state.get('observations', [])):
        r = o.get('result') or {}
        sid = r.get('source_id')
        if o.get('action') == 'read_source' and o.get('status') == 'completed' and sid and sid not in reads:
            text = r.get('text', '')[:12000]
            reads[sid] = {'source_id': sid, 'observation_id': o['id'], 'offset': r.get('offset', 0),
                          'text': text, 'excerpt_truncated': len(r.get('text', '')) > len(text),
                          'next_offset': r.get('offset', 0) + len(text)}
            if len(reads) == 6:
                break
    for sid, reading in reads.items():
        first = next((o for o in state.get('observations', []) if o.get('action') == 'read_source'
                      and o.get('status') == 'completed' and (o.get('result') or {}).get('source_id') == sid), None)
        if first and first['id'] != reading['observation_id']:
            reading['earlier_passage'] = {'observation_id': first['id'],
                'offset': first['result'].get('offset', 0), 'text': first['result'].get('text', '')[:3000],
                'note': 'Opening excerpt only; read_source can retrieve the remainder.'}
    return list(reads.values())


def action_payload(project):
    s = project.background_agent
    index, recent = observation_context(s)
    sources = [{k: c.get(k) for k in ('candidate_id', 'source_alias', 'url', 'title', 'retrieval_status', 'read_ranges', 'extraction_error')}
               for c in s.get('sources', [])[-100:]]
    return {'objective': project.objective, 'question': project.question.model_dump() if project.question else None,
            'research_mode': project.research_mode, 'scope': s['scope'], 'authorization': s.get('authorization'),
            'sources': sources, 'assessments': compact_result(s.get('assessments', {})),
            'observation_index': index, 'observations': recent, 'available_read_passages': reading_context(s),
            'feedback': s.get('feedback', [])[-4:], 'recovery': s.get('recovery'),
            'failed_hosts': sorted({urlsplit(c['url']).hostname for c in s.get('sources', [])
                                    if c.get('retrieval_status') == 'read_failed' and urlsplit(c['url']).hostname}),
            'server_state': {'saved_assessments': len(s.get('assessments', {})),
                             'can_finish_from_reading': any(c.get('shown_excerpts') for c in s.get('sources', [])),
                             'actions_used': len(s.get('observations', [])),
                             'convergence_reminder': 'Prefer a limited synthesis when further retrieval is unlikely to change the answer. finish consolidates reading automatically.',
                             'adopted_sources': len(project.evidence),
                             'last_action_status': recent[-1]['status'] if recent else None},
            'registered_assets': [{'asset_id': a.asset_id, 'filename': a.filename, 'purpose': a.purpose}
                                  for a in project.research_assets]}
