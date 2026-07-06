"""Tool registry for FlowScientist dialogue orchestration."""

from __future__ import annotations

from src.tools.base import Tool
from src.tools.report_tool import ReportTool
from src.tools.soft_swimmer_tool import SoftSwimmerExperimentTool


def get_default_tools() -> dict[str, Tool]:
    tools = [SoftSwimmerExperimentTool(), ReportTool()]
    return {tool.name: tool for tool in tools}


__all__ = ["ReportTool", "SoftSwimmerExperimentTool", "Tool", "get_default_tools"]
