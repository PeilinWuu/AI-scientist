# Quality Gate

- Original baseline: 161 passed, 1 third-party warning.
- Local sprint runtime: Python 3.12.7; CI target: Python 3.13.
- Current full suite: 175 passed, 1 third-party `StarletteDeprecationWarning`.
- New Competition 1B focused suite: 7 passed.
- New core-module statement coverage: 84% (560 statements, 88 missed).
- Coverage scope: `competition_schemas.py`, `competition_runtime.py`, and `tools/execution_adapter.py`.
- Syntax compilation: passed for all new modules and `app_streamlit.py`.
- FastAPI local smoke: passed, including actual demo execution and OpenAPI HTTP 200.
- Streamlit headless startup smoke: HTTP 200 with no observed traceback.

Coverage evidence is stored in `results/coverage.json`. CI gates the focused modules at 75%
without formatting or type-checking the entire legacy repository.
