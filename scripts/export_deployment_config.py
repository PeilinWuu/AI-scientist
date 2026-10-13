"""Export local verified settings for an authorized server transfer, never stdout.

The output contains credentials. Transfer only to your own server, install with
root:aiscientist 0640 permissions, then delete the temporary transfer copies.
"""
import argparse
import os
from pathlib import Path

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--enable-computation', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    defaults = dotenv_values(root / '.env.example')
    local = dotenv_values(root / '.env')
    values = {key: local.get(key, default) or '' for key, default in defaults.items()}
    if not values.get('DASHSCOPE_API_KEY'):
        raise RuntimeError('Local API key is missing')
    values.update({
        'AI_SCIENTIST_PROJECTS_DIR': '/var/lib/ai-scientist/projects',
        'RUNS_DIR': '/var/lib/ai-scientist/runs',
        'AI_SCIENTIST_COMPETITION_DIR': '/var/lib/ai-scientist/competition',
        'AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON': '0',
        'AI_SCIENTIST_ENABLE_COMPUTATION': '1' if args.enable_computation else '0',
    })
    # Single-line quoted EnvironmentFile format; no shell evaluation or logs.
    def quote(value):
        if '\n' in value or '\r' in value or '\x00' in value:
            raise ValueError('Multiline environment values are not supported')
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    payload = ''.join(f'{key}={quote(value)}\n' for key, value in values.items())
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(payload)
    print(f'Created restricted configuration file: {args.output} ({len(values)} settings)')


if __name__ == '__main__':
    main()
