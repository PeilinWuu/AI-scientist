"""Fail-closed Linux namespace runner. No application secrets or project tree mounted."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
import tempfile
from functools import lru_cache


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=1)
def runtime_status() -> dict:
    """Probe namespaces and scientific imports once per backend process."""
    try:
        with tempfile.TemporaryDirectory(prefix='scientist-runtime-') as directory:
            code = "import json, scipy, sklearn\nimport matplotlib.pyplot as plt\nplt.plot([0,1],[0,1]); plt.savefig('probe.png')\njson.dump({'metrics': {'runtime_ready': True}}, open('result.json', 'w'))"
            audit = ComputationSandbox(Path(directory), wall_seconds=10).execute(code, [], ['result.json'])
            return {'available': audit['status'] == 'success',
                    'reason': audit.get('error'), 'diagnostic': audit.get('stderr', '')[:1500],
                    'versions': audit.get('software_versions', {}), 'isolation': 'bubblewrap_namespaces'}
    except Exception as exc:
        return {'available': False, 'reason': str(exc), 'isolation': 'unavailable'}


class ComputationSandbox:
    def __init__(self, root: Path, wall_seconds: int = 90):
        self.root = root.resolve()
        self.wall_seconds = max(1, min(90, wall_seconds))

    @staticmethod
    def command(program: Path, inputs: Path, outputs: Path, seed: int) -> list[str]:
        binary = shutil.which('bwrap')
        if not binary:
            raise RuntimeError('Bubblewrap is required; host Python fallback is disabled.')
        scratch = program.parent / 'scratch'
        scratch.mkdir(exist_ok=True)
        cmd = [binary, '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
               '--clearenv', '--proc', '/proc', '--dev', '/dev', '--bind', str(scratch), '/tmp']
        for path in ('/usr', '/lib', '/lib64'):
            if Path(path).exists():
                cmd += ['--ro-bind', path, path]
        # Font discovery otherwise rebuilds the entire host font cache in /tmp.
        # Mount only public font configuration/cache, never the rest of /etc.
        for path in ('/etc/fonts', '/var/cache/fontconfig'):
            if Path(path).exists():
                cmd += ['--ro-bind', path, path]
        # Mount only the Python environment, never its parent project/home directory.
        if sys.prefix != sys.base_prefix:
            cmd += ['--ro-bind', sys.prefix, sys.prefix]
        cmd += ['--ro-bind', str(program), '/program.py',
                '--ro-bind', str(Path(__file__).with_name('computation_worker.py')), '/worker.py',
                '--ro-bind', str(inputs), '/input', '--bind', str(outputs), '/output',
                '--chdir', '/output', '--remount-ro', '/proc', '--remount-ro', '/dev', '--remount-ro', '/']
        for name, value in {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'MPLCONFIGDIR': '/tmp/mpl',
                            'MPLBACKEND': 'Agg', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
                            'MKL_NUM_THREADS': '1', 'RESEARCH_SEED': str(seed)}.items():
            cmd += ['--setenv', name, value]
        return cmd + [sys.executable, '-I', '/worker.py']

    def execute(self, code: str, assets: list[tuple[str, Path]], expected: list[str], seed: int = 0) -> dict:
        run_id = uuid.uuid4().hex
        run = self.root / 'computation' / run_id
        inputs, outputs = run / 'input', run / 'output'
        inputs.mkdir(parents=True)
        outputs.mkdir()
        input_hashes = {}
        for name, source in assets:
            if Path(name).name != name:
                raise ValueError('Invalid input filename')
            shutil.copyfile(source, inputs / name)
            input_hashes[name] = digest(inputs / name)
        program = run / 'program.py'
        program.write_text(code, encoding='utf-8')
        started = time.monotonic()
        status, error = 'failed', None
        with (run / 'stdout.txt').open('w') as out, (run / 'stderr.txt').open('w') as err:
            process = subprocess.Popen(self.command(program, inputs, outputs, seed), stdout=out, stderr=err,
                                       env={}, start_new_session=True)
            try:
                while process.poll() is None:
                    output_files = list(outputs.rglob('*'))
                    scratch_files = list((run / 'scratch').rglob('*'))
                    files = output_files + scratch_files
                    size = sum(p.lstat().st_size for p in files if not p.is_symlink())
                    if time.monotonic() - started > self.wall_seconds or len(output_files) > 64 or len(scratch_files) > 256 or size > 32 * 1024**2:
                        error = 'Execution time or output budget exceeded'
                        break
                    if (run / 'stdout.txt').stat().st_size + (run / 'stderr.txt').stat().st_size > 1024**2:
                        error = 'Log budget exceeded'
                        break
                    time.sleep(.1)
            finally:
                # Kill descendants even when their parent has already exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        result, artifacts = None, []
        try:
            if error:
                raise ValueError(error)
            if process.returncode != 0:
                raise ValueError('Program or namespace setup failed')
            files = list(outputs.rglob('*'))
            scratch_files = list((run / 'scratch').rglob('*'))
            if (len(files) > 64 or len(scratch_files) > 256
                    or sum(p.lstat().st_size for p in files + scratch_files) > 32 * 1024**2):
                raise ValueError('Output budget exceeded')
            for path in files:
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError('Only regular output files are accepted')
            names = {p.name for p in files}
            # Preserve safe partial outputs and the program's failure diagnosis.
            # Missing figures must not hide a KeyError already saved in result.json.
            for path in files:
                artifacts.append({'relative_path': str(path.relative_to(self.root)),
                                  'checksum_sha256': digest(path), 'size_bytes': path.stat().st_size})
            if 'result.json' in names:
                result = json.loads((outputs / 'result.json').read_text(),
                                    parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            if not set(expected + ['result.json']).issubset(names):
                missing = sorted(set(expected + ['result.json']) - names)
                failures = result.get('failures', []) if isinstance(result, dict) else []
                raise ValueError(f'Missing declared output artifacts: {missing}; program failures: {failures}')
            if isinstance(result, dict) and result.get('failures'):
                raise ValueError(f"Program reported failures: {result['failures']}")
            if not isinstance(result, dict) or not isinstance(result.get('metrics'), dict) or not result['metrics']:
                raise ValueError('Missing structured metrics')
            status = 'success'
        except (ValueError, OSError) as exc:
            error = str(exc)
        audit = {'run_id': run_id, 'status': status, 'error': error, 'result': result,
                 'software_versions': {name: importlib.metadata.version(name) for name in
                                       ('numpy', 'pandas', 'scipy', 'scikit-learn', 'matplotlib')},
                 'code_sha256': digest(program), 'input_hashes': input_hashes,
                 # Backward-compatible seed is the runner setting, NOT proof that
                 # generated code used it (default_rng can select its own seed).
                 'seed': seed, 'runner_seed': seed,
                 'seed_audit': {'runner_seed': seed, 'code_seed_verified': False,
                                'scope': 'Runner initializes numpy legacy RNG and RESEARCH_SEED; generated code may override either or create independent RNGs.'},
                 'duration_ms': int((time.monotonic() - started) * 1000),
                 'limits': {'wall_seconds': self.wall_seconds, 'cpu_seconds': 60,
                            'address_space_mb': 2048, 'output_mb': 32, 'output_files': 64,
                            'scratch_entries': 256},
                 'isolation': 'bubblewrap_namespaces', 'artifacts': artifacts,
                 'stdout': (run / 'stdout.txt').read_text(errors='replace')[:16000],
                 'stderr': (run / 'stderr.txt').read_text(errors='replace')[:16000],
                 'code_path': str(program.relative_to(self.root))}
        audit['executed_program'] = {
            'relative_path': audit['code_path'], 'checksum_sha256': audit['code_sha256'],
            'size_bytes': program.stat().st_size, 'role': 'executed_program',
            'description': 'Actual executed source saved by the platform, not a model-authored export.'}
        audit['artifact_package'] = [audit['executed_program'], *artifacts]
        (run / 'audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2))
        return audit
