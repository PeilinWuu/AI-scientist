# Evidence Index

| 核心主张 | 代码 | Test | JSON / Log / Figure | API / UI |
|---|---|---|---|---|
| 白名单执行且禁止任意代码 | `tools/execution_adapter.py`、`code_tools.py` | `test_executor_rejects_*` | execution request/result | readiness、Competition Demo |
| 数值与 provenance 可审计 | `competition_schemas.py` | `test_executor_validates_*` | checksum、fingerprint、software versions | artifacts endpoint |
| Round 2 引用 Round 1 | `competition_runtime.py` | `test_flagship_feedback_*` | `feedback/*.json`、run_state | demo state/history |
| 真实 RMSE 改善 | `competition_runtime.py` | flagship test | `iteration_comparison.json`、fit PNG | Competition Demo metrics |
| baseline 公平比较 | `run_benchmark()` | flagship test | `benchmark_summary.json` | comparison table |
| 5-seed 重复 | CLI | CLI exit code | `results/seed_runs/` | benchmark summary |
| 失败与 human review | `run_failure_cases()` | `test_failure_cases_*` | `failure_cases.json` | failure-cases endpoint/UI |
| API happy/failure | `competition_api.py` | `test_competition_api_*` | API test output | `/docs` |
| Qwen 路径真实存在 | structured/search clients | existing model tests | `qwen_smoke_evidence.json` | model endpoints |
| Qwen authenticated 调用成功 | `competition_readiness.run_qwen_smoke()` | `test_competition_qwen_readiness.py` | PASSED 脱敏证据、`QWEN_SMOKE_OK` | checklist |
