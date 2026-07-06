"""Final report generation agent."""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.llm.base import LLMProvider
from src.schemas import Constraints
from src.utils.llm_audit import LLMCallRecorder, evidence_from_raw


class ReportWriterAgent:
    """Builds a Markdown report and a structured JSON report."""

    def write(
        self,
        run_id: str,
        research_goal: str,
        constraints: Constraints,
        problem: dict[str, Any],
        history: list[dict[str, Any]],
        human_feedback: str | None,
        llm_metadata: dict[str, Any] | None = None,
        llm: LLMProvider | None = None,
        recorder: LLMCallRecorder | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Generate final report artifacts from the full iteration history."""

        llm_metadata = llm_metadata or {
            "llm_provider": "unknown",
            "llm_transport": "unknown",
            "llm_model": "unknown",
            "llm_base_url": "",
            "is_mock": True,
        }
        best_iteration, best_candidate = self._global_best(history, constraints.target_metric)
        next_plan = history[-1]["feedback"]["next_strategy"] if history else "No iteration completed."
        qwen_report_summary = ""
        report_evidence = None
        if llm:
            qwen_report_summary, report_evidence = self._call_report_writer_llm(
                run_id,
                research_goal,
                constraints,
                problem,
                history,
                human_feedback,
                llm,
                recorder,
            )
        total_llm_calls = recorder.count() if recorder else 0
        llm_calls_path = str(recorder.calls_dir) if recorder else ""

        lines = [
            "# FlowScientist-Loop Report",
            "",
            "## LLM Backend",
            f"- Provider: {self._provider_label(llm_metadata)}",
            f"- Model: {llm_metadata.get('llm_model', 'unknown')}",
            f"- Transport: {self._transport_label(llm_metadata)}",
            f"- Mock mode: {str(llm_metadata.get('is_mock', True)).lower()}",
            f"- Total LLM calls: {total_llm_calls}",
            f"- LLM call logs: {llm_calls_path}",
            "",
            "## 1. Project Title",
            "FlowScientist-Loop: Scientific Experiment Task Planning and Feedback Iteration",
            "",
            "## 2. Problem Statement",
            "Soft robotic swimmer design requires balancing propulsion speed, energy cost, stability, and flow loss. This prototype demonstrates a closed experiment loop rather than one-shot hypothesis generation.",
            "",
            "## 3. Research Goal",
            research_goal,
            "",
            "## 3a. Qwen Problem Analysis Summary",
            self._problem_summary(problem),
            "",
            "## 3b. Qwen Critic Feedback Summary",
            self._critic_summary(history),
            "",
            "## 3c. Qwen Report Writer Summary",
            qwen_report_summary or "No report-writer LLM summary was generated.",
            "",
            "## 4. Experimental Variables and Constraints",
            "- Variables: amplitude, frequency, wavelength, stiffness, phase.",
            f"- Minimum stability: {constraints.min_stability}",
            f"- Maximum energy cost: {constraints.max_energy_cost}",
            f"- Target metric: {constraints.target_metric}",
            f"- Human feedback: {human_feedback or 'None'}",
            "",
            "## 5. Agent Workflow",
            "ProblemAnalystAgent parses the goal and constraints. ExperimentPlannerAgent proposes parameter combinations. SimulationExecutor runs the lightweight virtual experiment. DataAnalystAgent selects the best measured design and identifies failures. CriticAgent converts failures into next-step guidance. ReportWriterAgent summarizes the complete loop.",
            "",
            "## 6. Iteration History",
        ]

        for item in history:
            analysis = item["analysis"]
            feedback = item["feedback"]
            best = analysis["best_candidate"]
            lines.extend(
                [
                    f"### Iteration {item['iteration']}",
                    f"- Plan strategy: {item['plan']['strategy']}",
                    f"- Best candidate: {best['candidate_id']} with efficiency={best['efficiency']}, mean_speed={best['mean_speed']}, energy_cost={best['energy_cost']}, stability_score={best['stability_score']}.",
                    f"- Analysis: {analysis['summary']}",
                    f"- Feedback: {feedback['message']}",
                    "",
                ]
            )

        lines.extend(
            [
                "## 7. Best Candidate Design",
                self._format_best(best_candidate, best_iteration),
                "",
                "## 8. Failure Analysis",
                self._failure_text(history),
                "",
                "## 9. Next-round Experimental Plan",
                f"The next round should {next_plan}. If connected to a higher-fidelity backend, the next plan should re-test the best local neighborhood with stricter convergence checks.",
                "",
                "## 10. Methods",
                "The current backend is a lightweight virtual experiment simulator. It encodes qualitative relationships between swimmer parameters and metrics, adds seeded noise for reproducibility, and applies hard energy/stability constraints.",
                "",
                "## 11. Experiments",
                f"The loop ran {len(history)} iterations. Each iteration saved a plan JSON file, a CSV result table, analysis JSON, and feedback JSON.",
                "",
                "## 12. Results",
                f"Best measured target value was obtained in iteration {best_iteration}. The selected design is reported above and should be treated as a virtual-screening candidate.",
                "",
                "## 13. Limitations",
                "This is not a high-fidelity CFD or FreeFlow simulation. The formulas are qualitative, the noise model is simple, and the reported candidate must be validated in a real simulator or experiment.",
                "",
                "## 14. References",
                "- [To be filled manually] Literature on soft robotic swimmers and flow-field optimization.",
                "- [To be filled manually] Literature on AI Scientist and multi-agent scientific discovery.",
                "",
            ]
        )

        report_json = {
            "run_id": run_id,
            "research_goal": research_goal,
            "constraints": constraints.model_dump(),
            "llm_backend": llm_metadata,
            "llm_evidence": report_evidence,
            "total_llm_calls": total_llm_calls,
            "llm_calls_path": llm_calls_path,
            "problem": problem,
            "human_feedback": human_feedback,
            "best_iteration": best_iteration,
            "best_candidate": best_candidate,
            "iteration_history": history,
            "limitations": [
                "Lightweight virtual experiment backend only.",
                "Replace formulas with FreeFlow or CFD adapter for real scientific use.",
            ],
        }
        return "\n".join(lines), report_json

    def _call_report_writer_llm(
        self,
        run_id: str,
        research_goal: str,
        constraints: Constraints,
        problem: dict[str, Any],
        history: list[dict[str, Any]],
        human_feedback: str | None,
        llm: LLMProvider,
        recorder: LLMCallRecorder | None,
    ) -> tuple[str, dict]:
        """Ask Qwen to summarize the final report evidence."""

        system_prompt = (
            "You are ReportWriterAgent. Summarize a real closed-loop experiment run. "
            "Use only supplied run evidence."
        )
        user_prompt = (
            f"run_id={run_id}\nresearch_goal={research_goal}\n"
            f"constraints={constraints.model_dump()}\nhuman_feedback={human_feedback}\n"
            f"qwen_problem_analysis={problem.get('qwen_problem_analysis', problem)}\n"
            f"iteration_history={history}\n"
            "Return a concise paragraph emphasizing real Qwen-guided planning and feedback."
        )
        try:
            if recorder:
                record = recorder.call("ReportWriterAgent", system_prompt, user_prompt)
                return record.raw_response, record.evidence
            raw = llm.generate(system_prompt, user_prompt)
            return raw, evidence_from_raw(llm, raw)
        except Exception:
            if settings.qwen_require_real:
                raise
            return "", evidence_from_raw(llm, "")

    def _provider_label(self, llm_metadata: dict[str, Any]) -> str:
        """Return a report-friendly backend label."""

        if llm_metadata.get("llm_provider") == "qwen":
            return "Qwen via Alibaba Cloud Model Studio / Bailian compatible API"
        if llm_metadata.get("is_mock"):
            return "MockLLMProvider development fallback"
        return str(llm_metadata.get("llm_provider", "unknown"))

    def _transport_label(self, llm_metadata: dict[str, Any]) -> str:
        """Return a report-friendly transport label."""

        transport = str(llm_metadata.get("llm_transport", "unknown"))
        if transport == "curl":
            return "curl fallback"
        return transport

    def _problem_summary(self, problem: dict[str, Any]) -> str:
        qwen = problem.get("qwen_problem_analysis", {})
        if not qwen:
            return "No Qwen problem analysis was recorded."
        return (
            f"Target metric: {qwen.get('target_metric')}. "
            f"Planning preference: {qwen.get('planning_preference')}. "
            f"Priority weights: {qwen.get('priority_weights')}. "
            f"Interpretation: {qwen.get('constraints_interpretation')}"
        )

    def _critic_summary(self, history: list[dict[str, Any]]) -> str:
        notes = []
        for item in history:
            feedback = item.get("feedback", {})
            notes.append(
                f"Iteration {item.get('iteration')}: "
                f"{feedback.get('diagnosis') or feedback.get('next_strategy')} "
                f"Adjustment: {feedback.get('parameter_adjustment')}"
            )
        return "\n".join(f"- {note}" for note in notes) if notes else "No critic feedback."

    def _global_best(self, history: list[dict[str, Any]], target_metric: str) -> tuple[int, dict]:
        """Find the best candidate across all iterations."""

        best_iteration = 0
        best_candidate: dict[str, Any] | None = None
        for item in history:
            candidate = item["analysis"]["best_candidate"]
            if best_candidate is None:
                best_iteration = item["iteration"]
                best_candidate = candidate
                continue
            current = candidate[target_metric]
            previous = best_candidate[target_metric]
            is_better = current < previous if target_metric == "energy_cost" else current > previous
            if is_better:
                best_iteration = item["iteration"]
                best_candidate = candidate
        return best_iteration, best_candidate or {}

    def _format_best(self, best: dict, iteration: int) -> str:
        """Format the best candidate section."""

        if not best:
            return "No candidate was evaluated."
        return (
            f"Iteration {iteration}, candidate {best['candidate_id']}: "
            f"amplitude={best['amplitude']}, frequency={best['frequency']}, "
            f"wavelength={best['wavelength']}, stiffness={best['stiffness']}, "
            f"phase={best['phase']}, efficiency={best['efficiency']}, "
            f"energy_cost={best['energy_cost']}, stability_score={best['stability_score']}."
        )

    def _failure_text(self, history: list[dict[str, Any]]) -> str:
        """Collect failure observations from all iterations."""

        notes: list[str] = []
        for item in history:
            for reason in item["analysis"]["failure_reasons"]:
                notes.append(f"- Iteration {item['iteration']}: {reason}")
        return "\n".join(notes) if notes else "No failures were recorded."
