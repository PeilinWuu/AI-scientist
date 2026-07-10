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
LLM_TIMEOUT=120
```

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
```

Do not assume every account supports the same model IDs. If a role variable is empty, the registry uses `AI_SCIENTIST_FALLBACK_MODEL`, then `LLM_MODEL`. Configuration or runtime fallback is recorded in the event log through `requested_model`, `actual_model`, and `fallback_used`; it is never silent.

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
