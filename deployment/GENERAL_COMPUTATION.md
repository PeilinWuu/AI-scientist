# General computation deployment and acceptance

This feature generates question-specific scientific Python using Qwen. Algorithms
are not selected by question keywords or example/project IDs. The existing damping
benchmark remains a separate deterministic example.

## Supported boundary

Tabular analysis and explicitly requested small mathematical simulations using
NumPy, Pandas, SciPy, scikit-learn and Matplotlib. Limits: 60 CPU seconds, 90 wall
seconds, 2 GiB address space, 32 MiB / 64 output files. No physical experiments,
network, package installation or large training jobs. Temporary library caches have
a separate 256-entry limit within the same 32 MiB combined disk budget; the 64-output
limit is unchanged. Missing essential data must
result in a request for input. Generated code/reviews can contain scientific errors;
execution success is not scientific proof.

## Ubuntu setup

```bash
sudo apt-get update
sudo apt-get install -y bubblewrap
.venv/bin/python -m pip install -r requirements.txt -r computation-requirements.txt
RUN_NAMESPACE_TESTS=1 .venv/bin/python -m pytest tests/ai_scientist/test_general_computation.py -q
```

Set `AI_SCIENTIST_ENABLE_COMPUTATION=1` in the server `.env` and restart the backend
and frontend. Keep `AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON=0`; the old runner is not
required. Host kernel and service policy must permit user namespaces. Failure to
create namespaces fails closed; generated code never falls back to host execution.

Use **one Uvicorn worker**. Jobs are in-process, not a distributed queue. Active
jobs block competing project mutations with HTTP 409. Restart recovery fails
orphaned jobs; regenerate a plan to retry interrupted computation. Deploy under a
dedicated service account and restrict access to authorized evaluators: this API
includes cost-incurring model calls and execution, and is not a multi-tenant service.

## Human frontend reproduction

1. Enter a new question without loading the damping example. Disable planning-only.
   Upload analytical data with purpose `data`, or explicitly request a mathematical simulation.
2. Advance the existing research planning, source selection and review stages.
   Resolve requests for input and approve the research plan.
3. In the execution-waiting stage, click **生成计算方案** (the main stage button),
   or **生成计算计划与代码** inside **执行阶段 · 计算方案与结果**.
   The page polls a background job. Inspect method, input filenames, metrics,
   validation checks, stopping rule and Python. `needs_input`/`unsupported` do not execute.
4. Check the approval checkbox and click **批准并执行计算**. Approval is tied to the
   exact plan and question/data/method fingerprint. Changed input invalidates it.
5. Inspect attempts and assessment. A programming-error repair can propose new
   code, but cannot change method, inputs, metrics or stopping rule. Changed code
   returns to approval and is not executed until you approve its new plan ID.
   An unchanged repair ends the attempt as failed; model-call budgets still apply.
6. If assessment requests another analysis, generate and approve the next plan.
   At most two scientific rounds are allowed; repairs are counted separately.
   Supply missing data when requested. Do not treat an exit code as a scientific finding.
7. Advance **数据分析 → 独立复审 → 人工批准 → 科学综合**. Computation cannot
   start from an early, review, synthesis or completed phase. Inspect/download
   plots, tables, program and computation audit. Reports include metrics and limitations.

## API equivalents

```bash
BASE_URL=http://127.0.0.1:8000
PROJECT_ID=project_REPLACE_WITH_APPROVED_PROJECT
curl -fsS "$BASE_URL/health" | jq
curl -fsS -X POST "$BASE_URL/api/research/$PROJECT_ID/computation/prepare" | jq
# Poll returned job_id:
curl -fsS "$BASE_URL/api/research/jobs/JOB_ID" | jq
curl -fsS "$BASE_URL/api/research/$PROJECT_ID" | jq '.computation'
# Use the exact inspected plan_id; this approves execution:
curl -fsS -X POST "$BASE_URL/api/research/$PROJECT_ID/computation/execute" \
  -H 'Content-Type: application/json' -d '{"plan_id":"REPLACE_WITH_PLAN_ID"}' | jq
```

`GET /api/research/{project_id}/computation/file?path=...` returns a recorded
artifact as filename plus base64 content. Only manifest-listed paths are accepted.
Computations persist code, input hashes, logs, library versions, output hashes and
repair history. `computation_model_receipt` artifacts record response/model/request
IDs when Qwen supplies them. These are application records, not signed attestations.

## Live component acceptance (consumes Qwen calls)

```bash
.venv/bin/python scripts/verify_general_computation.py --output /tmp/general-acceptance --case simulation
.venv/bin/python scripts/verify_general_computation.py --output /tmp/general-acceptance --case table
.venv/bin/python scripts/verify_general_computation.py --output /tmp/general-acceptance --case missing-data
```

These create explicitly marked approved fixtures, skipping upstream literature and
planning. They test real Qwen code generation and isolated execution through the
same service as the API, **not full frontend end-to-end success**. Table data is
explicitly synthetic; solution code is not supplied to Qwen. Repeat with unseen
questions and independently recompute metrics before making generality claims.
