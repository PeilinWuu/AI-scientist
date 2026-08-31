"""Controlled statistical operations; arbitrary formulas and code are rejected."""

from src.ai_scientist.tools.execution_adapter import ExecutionAdapter


class StatisticsTool(ExecutionAdapter):
    """Expose the implemented correlation and least-squares operations."""

    supported_operations = ("describe_dataset", "missingness", "correlation", "linear_regression")
