# FlowScientist-Loop Progress Update

## Current Status

The minimum runnable prototype of **FlowScientist-Loop** has been implemented and verified locally.

The project focuses on **scientific experiment task planning and feedback iteration**, not one-shot scientific hypothesis generation. It demonstrates a closed loop for soft robotic swimmer and flow-field optimization scenarios:

1. Research goal understanding
2. Experiment task planning
3. Lightweight virtual experiment execution
4. Data analysis
5. Feedback-based critique
6. Next-round experiment planning
7. Final report generation

## Implemented Components

### Backend

- Implemented a FastAPI backend in `src/main_api.py`.
- Added the following API endpoints:
  - `GET /health`
  - `POST /api/run`
  - `GET /api/runs/{run_id}`
  - `GET /api/runs/{run_id}/report`
  - `POST /api/runs/{run_id}/human_feedback`

### Frontend

- Implemented a Streamlit app in `app_streamlit.py`.
- The UI supports:
  - Research goal input
  - Max iteration setting
  - Constraint setting
  - Target metric selection
  - Optional human feedback
  - Running the full experiment loop
  - Displaying each iteration plan
  - Displaying result tables
  - Plotting best efficiency over iterations
  - Showing AI analysis and critic feedback
  - Showing and downloading final reports

### Multi-Agent Workflow

Implemented the following agents under `src/agents/`:

- `ProblemAnalystAgent`
  - Parses research goal, variables, constraints, priorities, and optional human feedback.

- `ExperimentPlannerAgent`
  - Plans experiment candidates.
  - Uses feedback-aware rules instead of random generation.
  - Adjusts parameters based on prior results:
    - High energy cost: reduce frequency or amplitude.
    - Low stability: reduce amplitude or increase stiffness.
    - Low speed: increase frequency or amplitude.
    - Improved target metric: continue local search around the best candidate.

- `DataAnalystAgent`
  - Analyzes results for each iteration.
  - Selects best candidate.
  - Reports failures and trends.

- `CriticAgent`
  - Converts analysis into next-round planning guidance.

- `ReportWriterAgent`
  - Generates final Markdown and JSON reports.

### Virtual Experiment Backend

Implemented `src/simulator/soft_swimmer_simulator.py`.

The simulator accepts:

- amplitude
- frequency
- wavelength
- stiffness
- phase

It outputs:

- mean_speed
- energy_cost
- efficiency
- stability_score
- vortex_loss
- constraint_violation

The current simulator is lightweight and qualitative. It includes seeded random noise for reproducibility.

### LLM Provider Abstraction

Implemented `src/llm/`:

- `MockLLMProvider`
  - Default provider.
  - Runs without any API key.

- `QwenProvider`
  - Reserved for DashScope/Qwen API usage.
  - Reads environment variables:
    - `DASHSCOPE_API_KEY`
    - `LLM_MODEL`, default `qwen-plus`
    - `LLM_BASE_URL`

No API keys are hard-coded.

### Persistence

Each run is saved under `runs/{run_id}/`.

Saved artifacts include:

- `config.json`
- `problem_analysis.json`
- `iteration_1_plan.json`
- `iteration_1_results.csv`
- `iteration_1_analysis.json`
- `iteration_1_feedback.json`
- subsequent iteration files
- `final_report.md`
- `final_report.json`

### Examples

Added three example cases under `examples/`:

- `case_efficiency_first.json`
- `case_low_energy.json`
- `case_human_feedback.json`

Added `run_examples.py`, which runs all example cases and prints report paths.

### Documentation

Added:

- `README.md`
- `requirements.txt`
- `.env.example`

README includes:

- Installation
- FastAPI startup
- Streamlit startup
- Qwen API configuration
- Example execution
- Explanation of how the project matches the experiment planning and feedback iteration direction
- Current simulator limitations
- How to replace the lightweight backend with a real FreeFlow / CFD adapter
- Reminder to manually replace placeholder references with real literature

## Verification Completed

The following checks were completed locally:

1. Ran `python run_examples.py`.
   - All three examples completed successfully.
   - Reports were generated under `runs/`.

2. Verified FastAPI with a smoke test.
   - `GET /health` returned `{"status":"ok"}`.
   - `POST /api/run` completed a one-iteration run successfully.

3. Verified code compilation for main modules.
   - `app_streamlit.py`
   - `src/main_api.py`
   - `src/workflow/experiment_loop.py`
   - `src/simulator/soft_swimmer_simulator.py`

4. Started local services successfully.
   - FastAPI: `http://127.0.0.1:8000`
   - Streamlit: `http://127.0.0.1:8501`

5. Created a packaged deliverable:
   - `outputs/FlowScientist-Loop-prototype.zip`

## Recommended PPT Screenshots

Suggested screenshots for presentation:

1. Streamlit main interface
   - Shows research goal, constraints, run button, iteration tables, efficiency plot, and final report.

2. `src/workflow/experiment_loop.py`
   - Shows the complete closed-loop orchestration.

3. `src/agents/experiment_planner.py`
   - Shows feedback-aware next-round planning logic.

4. `src/agents/critic.py`
   - Shows how results are converted into planning feedback.

5. `src/simulator/soft_swimmer_simulator.py`
   - Shows the lightweight virtual experiment backend.

6. A generated `runs/{run_id}/final_report.md`
   - Shows final report structure and results.

7. A generated `runs/{run_id}/iteration_2_plan.json`
   - Shows that later plans depend on earlier measured results.

## Current Limitation

The current version uses a **lightweight virtual experiment backend**. It is suitable for:

- Prototype demonstration
- Competition MVP
- Showing the AI Scientist feedback loop
- Explaining experiment planning logic

It is not a high-fidelity physical simulation.

The simulator should later be replaced by a real **FreeFlow / CFD adapter**. As long as the adapter returns the same metrics schema, the existing workflow, API, UI, and report generation can remain mostly unchanged.

## Next Steps

Recommended next improvements:

1. Add more visually polished charts for PPT.
2. Add a run comparison page in Streamlit.
3. Add a real FreeFlow / CFD adapter interface.
4. Add unit tests for planner feedback rules.
5. Replace placeholder report references with real verified literature.
6. Optionally enable Qwen API for richer textual analysis while keeping Mock mode as the default demo path.
