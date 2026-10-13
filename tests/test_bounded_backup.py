import io
from types import SimpleNamespace

import pytest

from deployment.bounded_backup import BoundedWriter, archives, prune


def test_retention_only_removes_old_matching_archives(tmp_path):
    for day in range(1, 10):
        (tmp_path / f'ai-scientist-202610{day:02d}-040000.tar.gz').write_bytes(b'123')
    untouched = tmp_path / 'user-data.json'
    untouched.write_text('retain')
    link = tmp_path / 'ai-scientist-20260101-040000.tar.gz'
    link.symlink_to(untouched)
    assert prune(tmp_path, keep=7, limit=15) == 15
    assert len(archives(tmp_path)) == 5
    assert untouched.read_text() == 'retain'
    assert link.is_symlink()


def test_stream_cap_prevents_oversized_partial_backup(tmp_path, monkeypatch):
    monkeypatch.setattr('deployment.bounded_backup.shutil.disk_usage',
                        lambda path: SimpleNamespace(free=100))
    raw = io.BytesIO()
    writer = BoundedWriter(raw, tmp_path, budget=5, reserve=10)
    writer.write(b'1234')
    with pytest.raises(OSError, match='size cap'):
        writer.write(b'56')
    assert raw.getvalue() == b'1234'


def test_disk_reserve_is_checked_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr('deployment.bounded_backup.shutil.disk_usage',
                        lambda path: SimpleNamespace(free=11))
    writer = BoundedWriter(io.BytesIO(), tmp_path, budget=100, reserve=10)
    with pytest.raises(OSError, match='Disk reserve'):
        writer.write(b'12')
