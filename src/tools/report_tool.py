"""Tool for generating a lightweight research-plan report from conversation state."""

from __future__ import annotations

from typing import Any

from src.tools.base import Tool


class ReportTool(Tool):
    """Generate a Markdown report skeleton from dialogue state."""

    name = "generate_research_plan_report"
    description = "Generate or update the research plan report from conversation evidence."
    schema = {
        "type": "object",
        "properties": {
            "research_goal": {"type": "string"},
            "planning_preference": {"type": "string"},
            "experiment_history": {"type": "array"},
            "llm_backend": {"type": "object"},
        },
    }

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        goal = arguments.get("research_goal", "Unspecified")
        preference = arguments.get("planning_preference", "unknown")
        history = arguments.get("experiment_history", [])
        backend = arguments.get("llm_backend", {})
        markdown = "\n".join(
            [
                "# FlowScientist Research Plan Report",
                "",
                "## LLM Backend",
                f"- Provider: {backend.get('llm_provider', backend.get('provider', 'unknown'))}",
                f"- Model: {backend.get('llm_model', backend.get('model', 'unknown'))}",
                f"- Transport: {backend.get('llm_transport', backend.get('transport', 'unknown'))}",
                f"- Mock mode: {str(backend.get('is_mock', backend.get('mock_mode', True))).lower()}",
                "",
                "## Research Goal",
                str(goal),
                "",
                "## Planning Preference",
                str(preference),
                "",
                "## Experiment History",
                str(history),
                "",
                "## Next Steps",
                "Use the latest tool results to refine candidate parameters and validate with a higher-fidelity FreeFlow/CFD backend.",
            ]
        )
        return {"tool_name": self.name, "markdown": markdown}
