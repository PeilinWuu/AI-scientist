"""Immutable figure references: a model chooses placement, never the file path."""
import hashlib
import re
from pathlib import Path, PurePosixPath

from src.ai_scientist.result_artifacts import _read_verified


FIGURE_INSTRUCTIONS = '''
available_figures lists actual files from a recorded successful computation, not
proof of visual inspection or scientific validity. When a figure helps explain
your findings, place its exact marker on a separate line in public_response (or
the report markdown), between the relevant paragraphs. Write your own useful
explanation around it. Do not put markers in code fences. Choose only supplied
markers, never invent a figure or claim you visually inspected it. Do not attach
an older run's figure as evidence for a newer run. Omit figures when irrelevant.
'''.strip()


def computation_attempts(project):
    state = project.get('computation') or {}
    records = [*state.get('history', []), *state.get('rounds', []), state]
    return [a for record in records for a in record.get('attempts', [])]


def clean_figure_refs(entries):
    """Whitelist public provenance only; paths must bind to one exact run."""
    cleaned = []
    for entry in entries[:32] if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        run_id, path, sha = (entry.get(k, '') for k in ('run_id', 'relative_path', 'checksum_sha256'))
        if not all(isinstance(v, str) for v in (run_id, path, sha)):
            continue
        relative = PurePosixPath(path)
        if (not re.fullmatch(r'[A-Za-z0-9_-]+', run_id)
                or relative.parent != PurePosixPath('computation') / run_id / 'output'
                or relative.suffix.lower() not in {'.png', '.jpg', '.jpeg'}
                or '\\' in path or not re.fullmatch(r'[0-9a-f]{64}', sha)):
            continue
        figure_id = 'fig_' + hashlib.sha256(f'{run_id}:{path}:{sha}'.encode()).hexdigest()[:24]
        cleaned.append({'figure_id': figure_id, 'marker': f'[[figure:{figure_id}]]',
                        'run_id': run_id, 'relative_path': path, 'checksum_sha256': sha})
    return cleaned


def figures_for_audit(audit):
    if audit.get('status') != 'success':
        return []
    return clean_figure_refs([{**item, 'run_id': audit.get('run_id')}
                              for item in audit.get('artifacts', [])])


def available_figures(project, root):
    """Offer only the latest successful run, verified before the model call."""
    audit = next((a for a in reversed(computation_attempts(project))
                  if a.get('status') == 'success'), {})
    result = []
    for figure in figures_for_audit(audit):
        try:
            _read_verified(Path(root).resolve(), figure['relative_path'], figure['checksum_sha256'])
        except (OSError, ValueError):
            continue
        result.append(figure)
    return result


def answer_blocks(text):
    """Split standalone references without changing prose or fenced code."""
    buffer, fence = [], None
    for line in text.splitlines(keepends=True):
        fence_match = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if fence_match:
            token = fence_match.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        marker = re.fullmatch(r'\s*\[\[figure:([^\]\n]+)\]\]\s*', line) if fence is None else None
        if marker:
            if buffer:
                yield 'text', ''.join(buffer)
                buffer = []
            yield 'figure', marker.group(1)
        else:
            buffer.append(line)
    if buffer:
        yield 'text', ''.join(buffer)


def export_figure_markdown(text, figures):
    """Saved reports use standard Markdown links, relative to artifacts/report.md."""
    refs = {f['figure_id']: f for f in clean_figure_refs(figures)}
    parts = []
    for kind, content in answer_blocks(text):
        if kind == 'text':
            parts.append(content)
        elif content in refs:
            path = refs[content]['relative_path']
            parts.append(f'![计算图表](<../{path}>)\n')
        else:
            parts.append('> 图表引用不可用。\n')
    return ''.join(parts)
