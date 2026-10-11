"""Entrypoint executed ONLY inside the OS-isolated computation workspace."""
import json
import os
import resource
import runpy


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024**2, 16 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    os.chdir('/output')
    import numpy as np
    np.random.seed(int(os.environ['RESEARCH_SEED']))
    runpy.run_path('/program.py', run_name='__main__')
    with open('/output/result.json', encoding='utf-8') as handle:
        result = json.load(handle, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(result, dict) or not isinstance(result.get('metrics'), dict) or not result['metrics']:
        raise ValueError('result.json must contain a nonempty metrics object')


if __name__ == '__main__':
    main()
