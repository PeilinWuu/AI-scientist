# Screenshot Checklist for Competition Materials

Use these screenshots to show that FlowScientist-Loop is a direction 1B experiment planning and feedback iteration system.

1. Streamlit main page
   - Capture research goal input, constraints, run button, iteration tables, efficiency curve, and final report area.

2. FastAPI `/docs` page
   - Capture available endpoints: `/health`, `/api/run`, `/api/runs/{run_id}`, `/api/runs/{run_id}/report`, `/api/runs/{run_id}/human_feedback`.

3. `/api/run` call result
   - Capture the returned `run_id` and summary.

4. `src/workflow/experiment_loop.py`
   - Capture the loop where plan, simulation, analysis, feedback, and report generation happen.

5. `src/agents/experiment_planner.py`
   - Capture the feedback-aware planning rules.

6. `src/agents/critic.py`
   - Capture the logic that converts measured failures into next-round guidance.

7. `src/simulator/soft_swimmer_simulator.py`
   - Capture the lightweight virtual experiment formulas and constraints.

8. `runs/{run_id}/` file structure
   - Capture saved config, per-iteration plans, CSV results, analysis, feedback, and reports.

9. `iteration_2_plan.json`
   - Capture evidence that later plans are generated from previous feedback.

10. `final_report.md`
    - Capture sections for problem statement, iteration history, best candidate, failure analysis, limitations, and references.

11. Qwen check success screenshot
    - Capture `python tools/check_qwen.py` output with model name, success status, and response preview. Do not show API keys.

12. `docs/demo_assets/{run_id}/03_best_efficiency_curve.png`
    - Capture the efficiency curve exported for PPT.
