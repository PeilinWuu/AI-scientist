"""Controlled dataset inspection interface."""

from src.ai_scientist.tools.execution_adapter import ExecutionAdapter


class DatasetTool(ExecutionAdapter):
    """Expose schema, descriptive and missingness operations from ExecutionAdapter."""

    supported_operations = ("inspect_dataset", "describe_dataset", "missingness")
