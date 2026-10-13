#!/usr/bin/python3
"""Root-owned daily backup: <=7 archives, <=2 GiB including rollback code.

Install at the existing backup service's ExecStart path. Never touches source
data during retention. Archives include secrets and must remain root-only.
"""
import datetime as dt
import fcntl
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

DEST = Path('/var/backups/ai-scientist')
LIMIT = 2 * 1024**3
RESERVE = 5 * 1024**3
KEEP = 7
ROLLBACK = Path('/opt/ai-scientist-previous')
PATTERN = re.compile(r'ai-scientist-\d{8}-\d{6}(?:-\d{6})?\.tar\.gz')


def archives(dest):
    return sorted((p for p in dest.iterdir() if PATTERN.fullmatch(p.name)
                   and p.is_file() and not p.is_symlink()), key=lambda p: p.name)


def prune(dest, keep=KEEP, limit=LIMIT):
    files = archives(dest)
    total = sum(p.stat().st_size for p in files)
    while files and (len(files) > keep or total > limit):
        old = files.pop(0)
        size = old.stat().st_size
        old.unlink()
        total -= size
        print(f'Retention removed {old.name} ({size} bytes)', flush=True)
    return total


def rollback_size(root=ROLLBACK):
    if root.is_symlink():
        raise RuntimeError('Rollback directory cannot be a symlink')
    return sum(p.stat().st_size for p in root.rglob('*')
               if p.is_file() and not p.is_symlink()) if root.exists() else 0


class BoundedWriter:
    def __init__(self, raw, dest, budget, reserve=RESERVE):
        self.raw, self.dest, self.budget, self.reserve = raw, dest, budget, reserve
        self.used = 0

    def write(self, data):
        if self.used + len(data) > self.budget:
            raise OSError('Backup size cap reached; previous archives retained')
        if shutil.disk_usage(self.dest).free - len(data) < self.reserve:
            raise OSError('Disk reserve reached; previous archives retained')
        count = self.raw.write(data)
        self.used += count
        return count

    def flush(self):
        self.raw.flush()


def main():
    os.umask(0o077)
    if DEST.is_symlink():
        raise RuntimeError('Backup directory cannot be a symlink')
    DEST.mkdir(parents=True, exist_ok=True, mode=0o700)
    DEST.chmod(0o700)
    with (DEST / '.backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        archive_limit = LIMIT - rollback_size()
        if archive_limit <= 0:
            raise RuntimeError('Backup paused: rollback code uses the 2 GiB budget')
        total = prune(DEST, limit=archive_limit)
        if shutil.disk_usage(DEST).free < RESERVE:
            raise RuntimeError('Backup paused: less than 5 GiB free')
        # Account for the temporary archive too. Do not delete valid archives
        # merely to make room for a new backup that could fail.
        budget = archive_limit - total
        if budget <= 0:
            raise RuntimeError('Backup paused: archive storage cap reached')
        sources = [Path('/var/lib/ai-scientist'), Path('/etc/ai-scientist')]
        for source in sources:
            if not source.is_dir() or source.is_symlink():
                raise RuntimeError(f'Invalid backup source: {source}')
        fd, name = tempfile.mkstemp(prefix='.pending-', suffix='.tar.gz', dir=DEST)
        pending = Path(name)
        try:
            with os.fdopen(fd, 'wb', buffering=0) as raw:
                writer = BoundedWriter(raw, DEST, budget)
                with tarfile.open(fileobj=writer, mode='w|gz', dereference=False) as tar:
                    for source in sources:
                        tar.add(source, arcname=str(source).lstrip('/'))
                raw.flush()
                os.fsync(raw.fileno())
            # Read the archive fully before publishing it as a valid backup.
            with tarfile.open(pending, 'r:gz') as tar:
                for member in tar:
                    if member.isfile():
                        with tar.extractfile(member) as content:
                            while content.read(1024 * 1024):
                                pass
            stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
            target = DEST / f'ai-scientist-{stamp}.tar.gz'
            pending.rename(target)
            prune(DEST, limit=archive_limit)
            print(f'Backup verified: {target.name} ({target.stat().st_size} bytes)', flush=True)
        finally:
            pending.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
