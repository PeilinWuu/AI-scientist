# FlowScientist

FlowScientist is a chat-first, Qwen-powered AI Scientist for **scientific experiment task planning and feedback iteration**. Its target positioning is broader than one simulator: it is a conversational AI Scientist for **general fluid simulation and flow-field optimization**, with a soft-swimmer virtual experiment tool as one available demonstration tool.

The primary product form is a conversational agent: the user discusses research goals and constraints with Qwen-powered FlowScientist, and FlowScientist decides when to clarify, plan experiments, call tools, analyze results, and revise the next plan.

## Competition Direction

This project corresponds to **direction 1B: scientific experiment task planning and feedback iteration**.

The system is organized around the conversational closed loop:

```text
User <-> Qwen-powered FlowScientist
  -> goal understanding
  -> constraint clarification
  -> experiment planning
  -> tool invocation
  -> result analysis
  -> next-round planning
  -> final research plan report
```

The virtual simulator is not the product itself. It is a tool used by the Qwen-powered AI Scientist to demonstrate experiment execution and feedback iteration. The system can later replace this tool with FreeFlow, CFD solvers, experiment-instrument APIs, or post-processing scripts.

## FlowScientist Is a Conversational AI Scientist, Not Just a Simulator

Qwen is the controller for dialogue, scientific planning, tool-use decisions, result interpretation, and iteration suggestions. Tools are callable execution or visualization modules. The current executable experiment tool is the soft-swimmer virtual experiment tool, and it is intentionally presented as a demonstration backend rather than as the whole product.

FlowScientist can be extended toward:

- soft-swimmer / robotic-fish propulsion efficiency optimization
- airfoil or wing aerodynamic optimization
- pipe-flow drag reduction
- microfluidic mixing efficiency optimization
- porous-media flow analysis
- vortex-shedding and bluff-body drag reduction
- thermal-flow coupled heat-transfer optimization
- hull or underwater-vehicle drag optimization

These are extensible application directions. The current prototype has implemented the soft-swimmer demo tool, plus a plotting tool for experiment-history visualization. It does not pretend that all CFD tools above are already connected.

## Agent Behavior Optimization

This project does not train a new foundation model. It improves agent behavior through a lightweight control layer around Qwen:

- **Skill policy**: prompts are modularized under `src/skills/` for base dialogue, intent routing, tool policy, experiment planning, result analysis, visualization, and readable responses.
- **Intent routing**: `src/agents/intent_router.py` calls Qwen for intent classification, then applies safety corrections so capability questions, conceptual explanations, and broad research consultations do not accidentally trigger tools.
- **Tool-use approval**: `src/policies/tool_use_policy.py` implements a code-level approval layer. Experiment tools are blocked for casual chat, capability questions, conceptual explanations, and research consultation. Experiment planning asks for confirmation before execution.
- **Dialogue evals**: `evals/dialogue_behavior_cases.yaml` and `tools/test_agent_behavior.py` provide behavior checks for intent, tool calls, and raw-JSON suppression.
- **Readable response guard**: `src/utils/readable_response.py` prevents raw tool JSON from appearing as the main chat response.

The design borrows ideas from Qwen-Agent tool-use planning, AgentScope evaluation practices, LangGraph-style human-in-the-loop approval, and DSPy-style modular prompts/pipelines, but intentionally avoids introducing those heavy frameworks in this MVP.

## Why This Is Not One-Shot Hypothesis Generation

FlowScientist-Loop does not stop after producing a single scientific suggestion. Each run saves iteration-level evidence:

- planned candidates
- simulated experiment results
- analysis of best and failed candidates
- critic feedback
- next-round planning strategy
- final Markdown and JSON report

Later plans are generated from previous measurements:

- If energy cost is too high, the planner lowers frequency or amplitude.
- If stability is too low, the planner lowers amplitude or increases stiffness.
- If speed is too low, the planner increases frequency or amplitude.
- If the target metric improves, the planner performs local search around the best measured candidate.

## Tech Stack

- Backend: FastAPI
- Frontend: Streamlit
- Data: pandas, numpy
- Charts: matplotlib
- Tests: pytest
- LLM provider abstraction:
  - `MockLLMProvider` by default, works without API keys.
  - `QwenProvider` for DashScope/Qwen API usage.

No LangChain, AutoGen, or other heavy agent framework is required.

## Project Structure

```text
FlowScientist-Loop/
  README.md
  requirements.txt
  .env.example
  app_streamlit.py
  run_examples.py
  tools/
    check_qwen.py
    export_demo_assets.py
  docs/
    competition_report_outline.md
    screenshot_checklist.md
    demo_assets/
  tests/
    test_simulator.py
    test_planner_feedback.py
    test_workflow.py
  src/
    main_api.py
    config.py
    schemas.py
    llm/
      base.py
      mock_provider.py
      qwen_provider.py
    agents/
      dialogue_orchestrator.py
      problem_analyst.py
      experiment_planner.py
      data_analyst.py
      critic.py
      report_writer.py
    simulator/
      base_adapter.py
      freeflow_csv_adapter.py
      soft_swimmer_simulator.py
    skills/
      base_dialogue_skill.py
      intent_router_skill.py
      experiment_planning_skill.py
      result_analysis_skill.py
      visualization_skill.py
      report_skill.py
    tools/
      base.py
      soft_swimmer_tool.py
      plot_tool.py
      report_tool.py
    workflow/
      experiment_loop.py
    utils/
      io.py
      plotting.py
  examples/
    case_efficiency_first.json
    case_low_energy.json
    case_human_feedback.json
  runs/
    .gitkeep
```

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On macOS/Linux, activate with:

```bash
source .venv/bin/activate
```

## How to Run Without API Key

No API key is required for the default demo path.

If `LLM_PROVIDER` is unset or set to `mock`, the project uses `MockLLMProvider`. This means the backend, Streamlit UI, examples, tests, and report generation can run offline.

```bash
python run_examples.py
streamlit run app_streamlit.py
```

## How to Enable Qwen API

Copy `.env.example` to `.env` and fill in your own values:

```env
DASHSCOPE_API_KEY=your_key_here
LLM_PROVIDER=qwen
LLM_MODEL=qwen-turbo
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_TRANSPORT=curl
QWEN_REQUIRE_REAL=true
QWEN_CURL_TIMEOUT=60
```

Do not hard-code keys in source code.

Check Qwen connectivity with:

```bash
python tools/check_qwen.py
```

The script prints the model name, success status, and the first 200 characters of the response. It does not print the full API key.

Directly test the curl-based provider used by the workflow:

```bash
python tools/test_curl_provider.py
```

Audit the latest run for real Qwen evidence:

```bash
python tools/audit_real_qwen_run.py
```

Run a goal-sensitivity check to verify that different goals change Qwen analysis and planned parameters:

```bash
python tools/test_goal_sensitivity.py
python tools/test_dialogue_goal_sensitivity.py
python tools/test_intent_router.py
python tools/test_readable_response.py
python tools/test_agent_behavior.py
```

## Using Real Qwen When Python HTTPS Fails

On some Windows/Anaconda machines, the Python OpenAI SDK or `requests` may fail with SSL EOF or connection reset errors even when Alibaba Cloud Bailian is reachable. If `curl.exe` can successfully call:

```text
https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
```

then use the curl transport for formal competition runs:

```env
LLM_PROVIDER=qwen
LLM_MODEL=qwen-turbo
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_TRANSPORT=curl
QWEN_REQUIRE_REAL=true
```

`QWEN_TRANSPORT=auto` tries OpenAI SDK, then `requests`, then curl. `QWEN_TRANSPORT=curl` directly uses the real Qwen API through `curl.exe`.

Mock mode is for development only. Do not use mock-generated outputs for competition submission. When `QWEN_REQUIRE_REAL=true`, the project will stop instead of silently producing mock results.

Confirm that a run is not mock-generated by checking:

- `GET /health`
- the Streamlit top status bar
- `runs/{run_id}/config.json`
- `runs/{run_id}/metadata.json`
- the `LLM Backend` section at the top of `runs/{run_id}/final_report.md`

## Run FastAPI Backend

```bash
uvicorn src.main_api:app --reload --host 127.0.0.1 --port 8000
```

Then open:

- Health check: <http://127.0.0.1:8000/health>
- API docs: <http://127.0.0.1:8000/docs>

Main endpoints:

- `GET /health`
- `POST /api/run`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/report`
- `POST /api/runs/{run_id}/human_feedback`

## Run Streamlit Frontend

```bash
streamlit run app_streamlit.py
```

The frontend is chat-first. Type natural-language questions, research goals, constraints, planning requests, explicit tool-execution requests, or visualization requests into the chat box. FlowScientist will:

- classify the current intent before deciding whether tools are allowed
- answer capability and conceptual questions without running experiments
- ask clarifying questions when information is insufficient
- update the visible research state in the sidebar
- propose experiment plans without auto-running them unless execution is explicit
- generate Qwen-designed experiment tool calls only when the user authorizes execution
- display natural-language tool summaries in chat
- keep raw tool JSON in hidden/collapsible audit payloads
- show generated figures directly when the plot tool is called
- analyze results and revise the next plan
- save `conversation.json`, `llm_calls/`, and `tool_calls/` under `runs/{run_id}/`

The audit panel shows total LLM calls, total tool calls, last Qwen response excerpt, last tool result, and whether the run is valid for competition submission.

## Run Examples

```bash
python run_examples.py
```

This runs:

- `examples/case_efficiency_first.json`
- `examples/case_low_energy.json`
- `examples/case_human_feedback.json`

Each run writes artifacts under `runs/{run_id}/`, including:

- `config.json`
- `problem_analysis.json`
- `iteration_1_plan.json`
- `iteration_1_results.csv`
- `iteration_1_analysis.json`
- `iteration_1_feedback.json`
- subsequent iteration files
- `final_report.md`
- `final_report.json`

## Run Tests

```bash
pytest
```

The tests cover:

- simulator output schema and reproducibility
- energy and stability constraint violations
- planner feedback rules
- workflow artifact persistence for multi-iteration runs
- intent routing and tool-use guard behavior
- readable response post-processing so raw JSON is not shown as chat content
- dialogue behavior eval cases under `evals/dialogue_behavior_cases.yaml`

## How to Generate Demo Assets

Export PPT-friendly assets from the latest run:

```bash
python tools/export_demo_assets.py
```

Export assets from a specific run:

```bash
python tools/export_demo_assets.py --run_id your_run_id
```

Assets are written to:

```text
docs/demo_assets/{run_id}/
```

Generated files:

- `01_config_summary.md`
- `02_iteration_summary.csv`
- `03_best_efficiency_curve.png`
- `04_best_candidate_table.csv`
- `05_agent_workflow_summary.md`
- `06_ppt_screenshot_checklist.md`

## How This Matches the Experiment Planning and Feedback Direction

The project is built around experiment task planning:

- `ProblemAnalystAgent` structures the goal, variables, constraints, and priorities.
- `ExperimentPlannerAgent` proposes concrete experiment parameter combinations.
- `SoftSwimmerSimulator` executes the lightweight virtual experiment.
- `DataAnalystAgent` ranks measured candidates and identifies failures.
- `CriticAgent` translates failures and trends into next-round guidance.
- `ReportWriterAgent` generates the final scientific-style report.

The run folder is an audit trail showing how the system moves from one round to the next.

## How to Replace Simulator With FreeFlow / CFD

The current system uses a **lightweight virtual experiment backend** in `src/simulator/soft_swimmer_simulator.py`.

The adapter interface is defined in:

- `src/simulator/base_adapter.py`
- `src/simulator/freeflow_csv_adapter.py`

`SimulationAdapter` exposes:

- `run_candidate(candidate, constraints) -> dict`
- `run_batch(candidates, constraints) -> pandas.DataFrame`

`FreeFlowCSVAdapter` reads a user-provided CSV with the same result schema:

- `amplitude`
- `frequency`
- `wavelength`
- `stiffness`
- `phase`
- `mean_speed`
- `energy_cost`
- `efficiency`
- `stability_score`
- `vortex_loss`

If a FreeFlow or CFD pipeline can output this schema, it can replace the lightweight simulator without rewriting the dialogue agent, API, or UI. In the chat-first architecture, a new solver should usually be added as a new tool module under `src/tools/`, with a corresponding adapter under `src/simulator/` or another integration layer.

## Suggested Screenshots for PPT/PDF

Recommended screenshots:

1. Streamlit main page.
2. FastAPI `/docs` page.
3. `/api/run` response with `run_id`.
4. `src/workflow/experiment_loop.py` closed-loop orchestration.
5. `src/agents/experiment_planner.py` feedback-aware planning logic.
6. `src/agents/critic.py` critique feedback logic.
7. `src/simulator/soft_swimmer_simulator.py` virtual experiment backend.
8. `runs/{run_id}/` artifact structure.
9. `iteration_2_plan.json`.
10. `final_report.md`.
11. `python tools/check_qwen.py` success output.
12. `docs/demo_assets/{run_id}/03_best_efficiency_curve.png`.

See also:

- `docs/competition_report_outline.md`
- `docs/screenshot_checklist.md`

## Known Limitations

The current simulator is a lightweight virtual experiment backend. It is useful for demos, workflow validation, and competition MVP presentation.

It is not a high-fidelity physical model:

- equations are qualitative and hand-designed
- noise is simple seeded random noise
- vortex loss is a surrogate metric
- no mesh, boundary condition, turbulence, or fluid-structure coupling solver is included
- generated references are placeholders and must be replaced manually with real verified literature

## Report References

The generated report contains placeholder references only:

- `[To be filled manually] Literature on soft robotic swimmers and flow-field optimization.`
- `[To be filled manually] Literature on AI Scientist and multi-agent scientific discovery.`

You must manually replace these placeholders with real verified literature before submitting a formal report or paper.
"# AI-scientist"  
