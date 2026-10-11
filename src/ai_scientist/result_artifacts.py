"""Bounded, integrity-checked observations of executed research artifacts.

These are untrusted research data, not agent instructions. File availability and
decodability checks do not establish scientific validity or visual correctness.
"""
from __future__ import annotations

import codecs
import csv
import hashlib
import io
import json
from pathlib import Path
import stat


MAX_FILE_BYTES = 32 * 1024**2
MAX_TOTAL_BYTES = 32 * 1024**2
MAX_PREVIEW_BYTES = 64 * 1024
MAX_CONTEXT_CHARS = 48000
TEXT_SUFFIXES = {'.txt', '.md', '.py', '.json', '.csv', '.tsv', '.log'}


def _read_verified(root: Path, relative: str, expected_hash: str, expected_size=None):
    relative_path = Path(relative)
    if (relative_path.is_absolute() or '..' in relative_path.parts
            or not relative_path.parts or '\\' in relative):
        raise ValueError('Unsafe artifact path')
    path = root
    for component in relative_path.parts:
        path = path / component
        if path.is_symlink():
            raise ValueError('Symlink artifacts are not accepted')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Artifact escaped project directory')
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Artifact is not a regular file')
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError('Artifact exceeds read budget')
    if expected_size is not None and expected_size != size:
        raise ValueError('Artifact size changed after execution')
    if not expected_hash:
        raise ValueError('Artifact has no execution checksum')
    checksum, prefix = hashlib.sha256(), bytearray()
    read_bytes = 0
    with path.open('rb') as handle:
        while chunk := handle.read(64 * 1024):
            read_bytes += len(chunk)
            if read_bytes > MAX_FILE_BYTES:
                raise ValueError('Artifact exceeds read budget')
            checksum.update(chunk)
            prefix.extend(chunk[:max(0, MAX_PREVIEW_BYTES - len(prefix))])
    if checksum.hexdigest() != expected_hash:
        raise ValueError('Artifact checksum changed after execution')
    return path, size, bytes(prefix)


def inspect_result_artifacts(root: Path, audit: dict, expected_outputs: list[str]) -> dict:
    """Read only files in this exact audited run; never execute their contents."""
    root = root.resolve()
    run_id = audit.get('run_id', '')
    if not run_id or Path(run_id).name != run_id or '\\' in run_id or run_id in {'.', '..'}:
        return {'artifacts': [], 'delivery_validation': {
            'status': 'needs_revision', 'issues': ['Execution has no valid run identifier'],
            'scientific_validity_checked': False, 'visual_review_pending': []}}
    run_prefix = Path('computation') / run_id
    expected_program = str(run_prefix / 'program.py')
    manifest = list(audit.get('artifacts', []))[:64]
    manifest.append({'relative_path': audit.get('code_path', ''),
                     'checksum_sha256': audit.get('code_sha256', ''), 'role': 'executed_program'})
    observations, issues, delivered, visual_pending = [], [], set(), []
    remaining_chars, remaining_bytes = MAX_CONTEXT_CHARS, MAX_TOTAL_BYTES
    for entry in manifest:
        relative = str(entry.get('relative_path', ''))
        item = {'relative_path': relative, 'role': entry.get('role', 'output'),
                'trust': 'untrusted_research_data', 'integrity_verified': False}
        observations.append(item)
        try:
            relative_path = Path(relative)
            if item['role'] == 'executed_program':
                if relative != expected_program:
                    raise ValueError('Executed source path does not match this run')
            elif relative_path.parent != run_prefix / 'output':
                raise ValueError('Output path does not match this run')
            candidate_size = (root / relative_path).lstat().st_size
            if candidate_size > remaining_bytes:
                raise ValueError('Total artifact read budget exhausted')
            path, size, prefix = _read_verified(root, relative, entry.get('checksum_sha256', ''),
                                                entry.get('size_bytes'))
            remaining_bytes -= size
            if size == 0:
                raise ValueError('Artifact is empty')
            item.update(filename=path.name, size_bytes=size, checksum_sha256=entry['checksum_sha256'],
                        integrity_verified=True)
            suffix = path.suffix.lower()
            # The approved/executed code is already a separate assessment field.
            if item['role'] == 'executed_program':
                codecs.getincrementaldecoder('utf-8')().decode(prefix, final=size <= len(prefix))
                item['description'] = 'Authoritative actual executed source, packaged by the platform.'
            elif suffix in TEXT_SUFFIXES:
                # A prefix can end inside a UTF-8 codepoint; drop only that tail.
                decoder = codecs.getincrementaldecoder('utf-8')()
                content = decoder.decode(prefix, final=size <= len(prefix))
                item['truncated'] = size > len(prefix)
                if suffix in {'.csv', '.tsv'}:
                    rows = list(csv.reader(io.StringIO(content), delimiter='\t' if suffix == '.tsv' else ','))
                    if not rows or not rows[0]:
                        raise ValueError('Table has no header')
                    # A partial final row must not be presented as a real record.
                    if item['truncated'] and content and not content.endswith(('\n', '\r')):
                        rows = rows[:-1]
                    if not rows:
                        raise ValueError('Table header exceeds the bounded preview budget')
                    rendered = json.dumps({'columns': rows[0][:40],
                                           'rows': [row[:40] for row in rows[1:121]]}, ensure_ascii=False)
                    item['truncated'] |= len(rows) > 121 or any(len(row) > 40 for row in rows)
                    item['format'] = 'table'
                else:
                    if suffix == '.json' and not item['truncated']:
                        json.loads(content)
                    rendered = content
                    item['format'] = 'text'
                item['preview'] = rendered[:remaining_chars]
                item['truncated'] |= len(rendered) > remaining_chars
                remaining_chars -= len(item['preview'])
            elif suffix == '.png':
                if not prefix.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('Invalid PNG signature')
                item['description'] = 'PNG signature and integrity checked; image content has NOT been visually inspected.'
                visual_pending.append(relative)
            else:
                item['description'] = 'Integrity checked only; contents require a format-specific reader.'
            delivered.add(path.name if item['role'] == 'output' else '__executed_program__')
        except (OSError, ValueError, UnicodeError, csv.Error) as exc:
            item['error'] = str(exc)
            issues.append(f'{relative or "artifact"}: {exc}')
    for filename in sorted(set(expected_outputs + ['result.json']) - delivered):
        issues.append(f'Missing or unreadable required output: {filename}')
    if '__executed_program__' not in delivered:
        issues.append('Actual executed program is not available with a verified checksum')
    return {'artifacts': observations, 'delivery_validation': {
        'status': 'needs_revision' if issues else 'complete', 'issues': issues,
        'scientific_validity_checked': False, 'semantic_review_required': True,
        'visual_review_pending': visual_pending,
        'scope': 'Required files, integrity, and supported-format readability only; not scientific acceptance.'}}
