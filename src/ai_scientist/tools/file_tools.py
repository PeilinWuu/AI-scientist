"""Safe project-boundary file interface."""

from src.ai_scientist.tools.execution_adapter import ExecutionAdapter


class FileTool(ExecutionAdapter):
    """Files are only read through typed dataset/simulation inputs under project_root."""

    supported_operations = ("inspect_dataset",)
