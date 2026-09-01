# Unified AI Scientist Entry Acceptance

Verified on 2026-09-01 through the sole Streamlit AI Scientist entry.

## Architecture conclusion

Before this acceptance pass, the editable damped-oscillator example and `CompetitionRuntime` were not
connected: the example only populated intake fields while the deterministic loop remained behind the
benchmark API. The final wiring uses explicit `constraints.example_case` metadata to bind
`damped_oscillator_v1`; it never matches question keywords or seed values.

Execution capabilities are persisted per project:

- `INTERNAL_EXECUTABLE`: an allowlisted deterministic executor is explicitly bound.
- `EXTERNAL_EXECUTION_REQUIRED`: no internal executor is available; researcher results are required.
- `PLANNING_ONLY`: the researcher explicitly requests a research plan without execution waiting.

## PATH A — external experiment

- Acceptance project: `project_44ddf55c311845ed8e3a3f44b845af97`.
- A generic coating experiment displayed `Researcher / external experiment required` and
  `EXTERNAL_EXECUTION_REQUIRED`; `executor_binding` remained null.
- A real `evaluation.json` was uploaded as Round 1, source `external_lab`, parsed locally, and registered as
  an experimental result.
- The project resumed through `DATA_ANALYSIS` to `CRITICAL_REVIEW`.
- Its `execution_analysis` artifact records `analysis_source=researcher_provided_external_result` and
  `generated_metrics={}`. No internal execution artifact or invented metric was produced.
- Previously completed project `project_cadebfdfdccc401798657eae73d58851` remains unchanged evidence for
  the subsequent independent review and synthesis path after researcher-supplied data.

## PATH B — internal deterministic executor

- Acceptance project: `project_afb5ef748eeb466c82a39e82f43e6f99`.
- Loading the example did not create a project or execute anything. The question and seed were edited in the
  browser; the explicit example binding remained until manually removed.
- Start Research attached and parsed `observations.csv` (360 rows, three columns) and displayed
  `Internal numerical simulation available`.
- The project displayed `EXECUTABLE` before execution. A browser-triggered executor invocation produced:
  Round 1 RMSE `0.057684`, Round 2 RMSE `0.033089`, relative improvement `42.64%`.
- The project audit summary contains the Round 1 `ExecutionResult`, artifact-bound `FeedbackSignal`, old/new
  `PlanAdjustment`, Round 2 `IterationRecord`, and comparison.
- Project-local runtime artifacts are stored under `internal_execution/damped_oscillator`; existing benchmark
  evidence under `competition/1b/cases/flagship` was not rewritten.
- The unified executor consumes the latest compatible project observation CSV. Changing a project attachment
  therefore changes the hashed execution input instead of being overwritten by generated benchmark data.

The acceptance harness reused already verified planning artifacts to position the two new acceptance projects
at `EXECUTION_WAITING`; it did not rerun the 25-call Qwen planning/review chain or alter the completed project.
All execution and upload/resume actions described above were then triggered through the Streamlit UI.
