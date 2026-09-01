"""Explicit capability registry; availability never implies execution success."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ToolDescriptor:
    tool_name: str
    capability: str
    accepted_inputs: list[str]
    produced_outputs: list[str]
    timeout: int
    safety_level: str
    available: bool
    requires_human_approval: bool


class ToolRegistry:
    """List connected and placeholder research capabilities."""

    def __init__(self) -> None:
        controlled_python_enabled = os.getenv("AI_SCIENTIST_ENABLE_CONTROLLED_PYTHON", "0").lower() in {
            "1", "true", "yes", "on",
        }
        self._tools = {
            "web_search": ToolDescriptor(
                "web_search", "Retrieve current public web evidence", ["query"], ["reply", "sources"], 120, "network", True, False
            ),
            "web_extractor": ToolDescriptor(
                "web_extractor", "Extract content from public pages selected by search", ["url"], ["text", "sources"], 120, "network", True, False
            ),
            "file_search": ToolDescriptor(
                "file_search", "Inspect user-provided files", ["artifact_id"], ["excerpts"], 60, "local_read", True, True
            ),
            "dataset_inspector": ToolDescriptor(
                "dataset_inspector", "Inspect a supplied dataset schema and quality", ["artifact_id"], ["profile"], 60, "local_read", True, True
            ),
            "python_executor": ToolDescriptor(
                "python_executor", "Run restricted scientific Python in an audited child process", ["code", "registered_dataset"], ["logs", "results", "artifacts"], 30, "restricted_execution", controlled_python_enabled, True
            ),
            "statistical_analyzer": ToolDescriptor(
                "statistical_analyzer", "Run a whitelisted deterministic statistical analysis", ["operation", "dataset", "parameters"], ["results", "artifacts"], 120, "execution", True, True
            ),
            "categorical_analyzer": ToolDescriptor(
                "categorical_analyzer", "Compute bounded frequencies, grouped summaries, contingency tables, and seeded group comparisons", ["dataset", "columns"], ["tables", "metrics", "artifacts"], 120, "execution", True, True
            ),
            "time_series_analyzer": ToolDescriptor(
                "time_series_analyzer", "Compute deterministic temporal coverage, trend, and lag summaries", ["dataset", "time_column", "value_columns"], ["series_summary", "artifacts"], 120, "execution", True, True
            ),
            "text_analyzer": ToolDescriptor(
                "text_analyzer", "Compute bounded lexical corpus summaries without semantic inference", ["dataset", "text_columns"], ["token_counts", "artifacts"], 120, "execution", True, True
            ),
            "data_visualizer": ToolDescriptor(
                "data_visualizer", "Render deterministic histogram and scatter plot artifacts", ["dataset", "columns"], ["png_artifacts"], 120, "execution", True, True
            ),
            "code_runner": ToolDescriptor(
                "code_runner", "Run versioned research code", ["code_artifact", "parameters"], ["logs", "results"], 120, "execution", False, True
            ),
            "citation_manager": ToolDescriptor(
                "citation_manager", "Normalize and validate supplied citations", ["sources"], ["citations"], 30, "metadata", False, False
            ),
            "artifact_store": ToolDescriptor(
                "artifact_store", "Persist structured research artifacts", ["content", "type"], ["artifact_id"], 30, "local_write", True, False
            ),
        }

    def get(self, name: str) -> ToolDescriptor:
        return self._tools[name]

    def available_names(self) -> list[str]:
        return [name for name, descriptor in self._tools.items() if descriptor.available]

    def unavailable_names(self) -> list[str]:
        return [name for name, descriptor in self._tools.items() if not descriptor.available]

    def capabilities(self) -> dict[str, object]:
        return {
            "available_tools": self.available_names(),
            "unavailable_tools": self.unavailable_names(),
            "tools": {name: asdict(tool) for name, tool in self._tools.items()},
        }
