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

Evidence acquisition is bounded and resumable. Evidence Researcher first creates an offline `SearchPlan`, runs each query independently with `web_search` only, deterministically selects a limited source set, extracts selected URLs in small streaming batches, and finally normalizes evidence without network tools. Versioned `search_checkpoint_vN.json` artifacts prevent completed queries from being repeated after interruption.

The current release does not connect a real laboratory, simulation, code-execution, or statistical-analysis backend. `ExecutionAdapter.execute()` raises `NotImplementedError`. Without a real backend, projects wait for human data or execution and do not generate experimental data, analysis results, or scientific conclusions. Planning-only synthesis explicitly states that the plan has not been executed.

## Installation

```bash
pip install -r requirements.txt
```

Create `.env` from `.env.example` and set your API key:

```env
DASHSCOPE_API_KEY=
LLM_MODEL=qwen-turbo
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
AI_SCIENTIST_SEARCH_ACQUISITION_MODEL=
AI_SCIENTIST_SEARCH_FALLBACK_MODEL=
```

Do not assume every account supports the same model IDs. If a role variable is empty, the registry uses `AI_SCIENTIST_FALLBACK_MODEL`, then `LLM_MODEL`. Configuration or runtime fallback is recorded in the event log through `requested_model`, `actual_model`, and `fallback_used`; it is never silent.

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

```bash
pytest -q
```

Tests cover skill validation and core neutrality, state-machine transitions, atomic persistence, structured repair, model fallback, ClaimGraph traceability, reviewer gates, execution boundaries, cross-domain mode routing, API safety, and regression protection for Pure Qwen and Qwen Search.
