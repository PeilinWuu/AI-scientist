# Qwen Research Shell

This project provides three isolated modes on one FastAPI and Streamlit application.

## Modes

### Pure Qwen

`POST /api/chat` is a direct Chat Completions pass-through. It loads no skills, tools, research state, or hidden instructions. The Qwen call sends only `model` and visible `user`/`assistant` messages.

### Qwen Search

`POST /api/chat_search` uses the existing Responses API implementation with Qwen's built-in `web_search` and `web_extractor` tools. Search continuity uses `previous_response_id`; tool output is kept separate from the final assistant text.

### AI Scientist

AI Scientist is an independent, domain-neutral research-planning workflow under `src/ai_scientist/`. It does not modify or inject content into Pure Qwen or Qwen Search.

It combines:

- role-specific Qwen model calls;
- a one-stage-at-a-time state machine;
- validated Pydantic outputs;
- three-layer skills;
- claim-evidence traceability;
- independent skeptical review;
- human approval and revision nodes;
- atomic project persistence and append-only events;
- explicit execution capability boundaries.

Agents do not hold a free-form group chat. `ResearchOrchestrator` calls one stage in sequence, and roles exchange only validated structured artifacts. Hidden reasoning, raw provider responses, complete prompts, credentials, and authorization headers are never persisted or returned by the research API.

## AI Scientist Architecture

The research roles are:

- Research Director
- Evidence Researcher
- Methodologist
- Hypothesis Scientist
- Study Designer
- Analyst
- Reproducibility Engineer
- Skeptical Reviewer
- Scientific Synthesizer

The Reviewer is an independent Qwen call that receives structured project state only. A critical score below 6 cannot produce an `approve` decision.

### Independent review and human-controlled revision

Reviewer revision requests stop at `HUMAN_REVISION_REVIEW`; they do not immediately rewrite artifacts. The workspace classifies issues as planning blockers, execution prerequisites, non-blocking improvements, or optional suggestions. A human can accept the AI proposal, modify it, provide exact content, accept an issue as a limitation, defer it to execution, or reject it with a reason.

Accepted issues are grouped by target artifact and executed as batches. A successful model response only creates a new artifact version. Completion requires deterministic checks followed by an independent structured verifier call. Failed verification returns the project to human review instead of silently advancing. `AI_SCIENTIST_REVISION_VERIFIER_MODEL` can assign a separate Qwen model to this verification role.

Each role call loads exactly four selected skill files:

1. `skills/core/epistemic_policy.yaml`
2. the current role skill
3. the selected method skill
4. the selected domain skill

Method skills contain research-method rules but no discipline-specific knowledge. Domain skills are optional plugins and cannot override the epistemic policy. Fluid dynamics is one ordinary plugin; it is not the system default or a dependency of the core workflow.

## Capability Boundary

The current release can:

- formulate research questions;
- retrieve background evidence with the existing Qwen Search client;
- map claims to explicit evidence;
- generate falsifiable hypotheses;
- select methods and domains;
- create study, analysis, and reproducibility plans;
- run independent review;
- synthesize an approved planning-only research report.

Evidence acquisition is bounded, resumable, and human-curated. Evidence Researcher first creates an offline `SearchPlan` bound to the project and active research-question hash. In the default `ASSISTED` mode, the researcher must approve or edit that plan before any network request. Bounded queries then create `SourceCandidate` records, not evidence. The UI shows source metadata, AI recommendations, relevance, and verification signals; a human decides which candidates to keep, reject, or defer. Only kept candidates are extracted in small streaming batches, verified, and normalized into formal `EvidenceItem` records. Versioned checkpoints are bound to `project_id`, `question_hash`, and `search_plan_id`, so a plan or checkpoint cannot cross projects or questions.

The governing rule is: **AI discovers sources, the researcher decides whether to adopt them, and the system verifies and builds the evidence chain.** Search results never become evidence automatically in `ASSISTED` or `MANUAL` mode. Supported uploaded research files are parsed locally into bounded, auditable context; parsing does not verify scientific claims or execute an analysis.

The current release does not connect a real laboratory, simulation, code-execution, or statistical-analysis backend. `ExecutionAdapter.execute()` raises `NotImplementedError`. Without a real backend, projects wait for human data or execution and do not generate experimental data, analysis results, or scientific conclusions. Planning-only synthesis explicitly states that the plan has not been executed.

## Installation

Create an isolated environment, install the declared dependencies, and run the
test suite before starting the services. The commands below are verified with
Python 3.13.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.lock.txt
.\.venv\Scripts\python -m pytest -q
```

macOS or Linux:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.lock.txt
./.venv/bin/python -m pytest -q
```

`requirements.lock.txt` is the exact dependency set used for verification.
Use `requirements.txt` only when intentionally resolving newer compatible
versions, then refresh and retest the lock file.

Copy `.env.example` to `.env` and set your API key before making a real model
request. The API and UI can start, and the local tests can run, without a key.

```env
DASHSCOPE_API_KEY=
LLM_MODEL=qwen3.8-max
LLM_SEARCH_MODEL=qwen3.7-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
RESPONSES_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_SEARCH_ENABLE_WEB_EXTRACTOR=auto
LLM_TIMEOUT=120
```

Search tool compatibility depends on the API gateway. With `QWEN_SEARCH_ENABLE_WEB_EXTRACTOR=auto`, the official DashScope endpoint sends both `web_search` and `web_extractor`; other OpenAI-compatible gateways send only `web_search`. Set the value explicitly to `true` or `false` only when the gateway documentation confirms its supported Responses API tools.

## Configure AI Scientist Models

Configure Qwen model IDs available to your own Alibaba Cloud Model Studio account:

```env
AI_SCIENTIST_DIRECTOR_MODEL=
AI_SCIENTIST_RESEARCH_MODEL=
AI_SCIENTIST_METHODOLOGIST_MODEL=
AI_SCIENTIST_HYPOTHESIS_MODEL=
AI_SCIENTIST_DESIGNER_MODEL=
AI_SCIENTIST_ANALYST_MODEL=
AI_SCIENTIST_REPRODUCIBILITY_MODEL=
AI_SCIENTIST_REVIEWER_MODEL=
AI_SCIENTIST_SYNTHESIZER_MODEL=
AI_SCIENTIST_FALLBACK_MODEL=

AI_SCIENTIST_MAX_MODEL_CALLS=50
AI_SCIENTIST_MAX_ITERATIONS=2
AI_SCIENTIST_PROJECTS_DIR=data/research_projects
AI_SCIENTIST_STRUCTURED_RETRY=1
AI_SCIENTIST_DEFAULT_PLANNING_ONLY=true
AI_SCIENTIST_SEARCH_QUERY_TIMEOUT=120
AI_SCIENTIST_EXTRACTION_TIMEOUT=300
AI_SCIENTIST_SEARCH_TOTAL_BUDGET=600
AI_SCIENTIST_MAX_SEARCH_QUERIES=4
AI_SCIENTIST_MAX_SEARCH_RESULTS_PER_QUERY=5
AI_SCIENTIST_MAX_EXTRACTED_SOURCES=8
AI_SCIENTIST_MIN_USABLE_SOURCES=3
AI_SCIENTIST_MIN_CURATED_SOURCES=1
AI_SCIENTIST_MIN_VERIFIED_EVIDENCE=1
AI_SCIENTIST_SEARCH_ACQUISITION_MODEL=
AI_SCIENTIST_SEARCH_FALLBACK_MODEL=
```

Do not assume every account supports the same model IDs. If a role variable is empty, the registry uses `AI_SCIENTIST_FALLBACK_MODEL`, then `LLM_MODEL`, and finally the built-in `qwen-turbo` default. Configuration or runtime fallback is recorded in the event log through `requested_model`, `actual_model`, and `fallback_used`; it is never silent.

The Streamlit UI no longer uses a fixed model dropdown. Pure Qwen, Qwen Search, and every AI Scientist role accept a free-form model ID. Values in `.env` are defaults only; frontend edits affect the current browser session, and AI Scientist model overrides are saved into the project at creation time. Updating `.env` later does not silently change an existing research project.

Model names are lightly sanitized before use: empty input falls back to the default, while line breaks, control characters, and names longer than 128 characters are rejected. The app does not guess, correct, or validate against a hard-coded model list. Use the built-in test buttons or the API below to verify real account support.

```text
GET  /api/config/models
POST /api/models/test
```

`POST /api/models/test` accepts:

```json
{"model": "qwen3.7-plus", "mode": "chat"}
```

or:

```json
{"model": "qwen3.7-plus", "mode": "search"}
```

It returns a safe success/error category and never returns API keys or authorization headers.

## Run

Backend:

```bash
python -m uvicorn src.main_api:app --reload --host 127.0.0.1 --port 8000
```

Frontend:

```bash
streamlit run app_streamlit.py
```

Select `Pure Qwen`, `Qwen Search`, or `AI Scientist` in the sidebar.

### Project reference and data uploads

AI Scientist accepts multiple project files when a project is created and at the search-plan,
source-selection, independent-revision, and final-approval review gates. Supported extensions are
PDF, Markdown, TXT, CSV, TSV, JSON, XML, XLSX, and XLS. Every file is stored under the project's
`assets/` directory with its original filename, purpose (`reference`, `data`, or `other`), optional
description, upload context, size, and audit event. The default per-file limit is 25 MiB and can be
changed with `AI_SCIENTIST_MAX_ASSET_BYTES`.

Registered filenames are clickable in the Streamlit project workspace. The backend resolves each
request by project ID and asset ID, validates that the file remains inside the project directory,
and returns it inline when the browser supports that format.

Supported files are parsed locally after registration unless parsing is disabled. Parsed summaries,
schemas, bounded samples, and excerpts can enter structured agent context and record which roles used
them. A failed or unsupported parse remains visibly `registered_only`; parsing never turns a document
into verified evidence and never executes statistical analysis.

## AI Scientist API

Create a planning-only project:

```bash
curl -X POST http://127.0.0.1:8000/api/research/start ^
  -H "Content-Type: application/json" ^
  -d "{\"objective\":\"Evaluate whether a new classification algorithm outperforms existing methods.\",\"domain_hint\":\"computer_science\",\"constraints\":{},\"max_iterations\":2,\"planning_only\":true}"
```

You can pin a model team for one project with `model_overrides`:

```json
{
  "objective": "Evaluate whether a new classification algorithm outperforms existing methods.",
  "domain_hint": "computer_science",
  "constraints": {},
  "max_iterations": 2,
  "planning_only": true,
  "model_overrides": {
    "research_director": "qwen-plus",
    "evidence_researcher": "qwen3.7-plus",
    "fallback": "qwen-plus"
  }
}
```

Advance exactly one phase:

```bash
curl -X POST http://127.0.0.1:8000/api/research/PROJECT_ID/step
```

Inspect or control the project:

```text
GET  /api/research/{project_id}
POST /api/research/{project_id}/approve
GET  /api/research/{project_id}/search-plan
POST /api/research/{project_id}/search-plan/approve
POST /api/research/{project_id}/search-plan/regenerate
GET  /api/research/{project_id}/source-candidates
POST /api/research/{project_id}/source-selection
POST /api/research/{project_id}/human-sources
POST /api/research/{project_id}/research-assets
GET  /api/research/{project_id}/research-assets/{asset_id}
POST /api/research/{project_id}/research-assets/{asset_id}/parse
POST /api/research/{project_id}/revise
POST /api/research/{project_id}/provide-data
POST /api/research/{project_id}/cancel
GET  /api/research/{project_id}/claims
GET  /api/research/{project_id}/evidence
GET  /api/research/{project_id}/hypotheses
GET  /api/research/{project_id}/artifacts
GET  /api/research/{project_id}/events
GET  /api/research/{project_id}/capabilities
```

Projects persist under:

```text
data/research_projects/{project_id}/project.json
data/research_projects/{project_id}/events.jsonl
data/research_projects/{project_id}/artifacts/
```

`project.json` is atomically replaced. `events.jsonl` is append-only.

## Local Research File Parsing

Project creation and every human-review entry can accept PDF, Markdown, TXT, CSV, TSV, JSON, XML,
XLSX, and legacy XLS files. By default, an upload is saved first and then parsed locally into a
bounded, auditable artifact. The original file remains available through its project-scoped link,
and a failed parse can be retried without uploading the file again.

Parsed content is not decorative metadata. Every structured research role receives a bounded
`uploaded_asset_context` containing the asset ID, purpose, parser summary, content digest, structured
table/schema information, and a text excerpt. Completed role calls record the parsed artifact as an
input and append the role name to `research_assets[].used_by_agents`. The final report includes this
provenance without copying full uploaded content.

The global epistemic skill treats file content as untrusted research material, never instructions.
Parsing does not independently verify a paper and does not execute statistical analysis. CSV and
Excel parsing provides columns, missing-value summaries, and bounded samples so the Analyst can
design a data-grounded analysis plan; it cannot report results until a real analysis tool is connected.
Likewise, scanned PDFs without a text layer require a future OCR adapter. Parser and context limits are
configured with the `AI_SCIENTIST_ASSET_*` settings in `.env.example`.

## Extend AI Scientist

### Add an Agent

1. Add a role model environment variable to `model_registry.py` and `.env.example`.
2. Add a complete core role skill YAML.
3. Implement a `BaseResearchAgent` subclass with a Pydantic output schema.
4. Register one explicit phase handler in `ResearchOrchestrator`.
5. Add schema, isolation, event, and failure tests.

### Add a Method Skill

Create a YAML file in `skills/methods/` with all required fields, then map the relevant `ResearchMode` in `skill_loader.py`. Keep it discipline-neutral.

### Add a Domain Skill

Create a YAML file in `skills/domains/`, add conservative routing signals, and write tests proving that low-confidence routing falls back to `general`. A domain plugin cannot modify the state machine or epistemic policy.

### Add a Tool

Add a `ToolDescriptor` to `tools/registry.py`, implement a bounded adapter, declare safety and approval requirements, and return structured outputs. Mark it unavailable until a real backend exists. Never use a placeholder to generate pretend results.

## Competition 1B deterministic feedback demo

Run the reproducible five-seed damped-oscillator benchmark, its one-shot baseline,
and controlled failure cases without a model credential:

```powershell
python -m src.ai_scientist.competition_cli run-flagship --output competition/1b
```

The FastAPI routes are under `/api/competition/1b`, and Streamlit exposes a
`Competition Demo / 反馈迭代` mode. Submission evidence and reproduction instructions
are under `competition/1b/`. Arbitrary Python or LLM-generated code is never executed.

Run one authenticated Qwen competition smoke test explicitly, then reuse its redacted evidence
without paying for another API call on each readiness check:

```powershell
python -m src.ai_scientist.competition_readiness --run-qwen-smoke
python -m src.ai_scientist.competition_readiness
```

## Existing API Verification

Pure Qwen remains available at:

```text
GET  /api/qwen_ping
POST /api/debug_payload
POST /api/chat
```

Qwen Search remains available at:

```text
GET  /api/search_ping
POST /api/debug_search_payload
POST /api/chat_search
```

## Tests

AI Scientist 项目的时间戳始终以 UTC 持久化。前端通过 `UI_TIMEZONE` 转换显示，默认值为 `Asia/Shanghai`；开发者调试区会同时显示本地时间和 UTC，便于审计。

```bash
pytest -q
```

Tests cover skill validation and core neutrality, state-machine transitions, atomic persistence, structured repair, model fallback, ClaimGraph traceability, reviewer gates, execution boundaries, cross-domain mode routing, API safety, and regression protection for Pure Qwen and Qwen Search.
