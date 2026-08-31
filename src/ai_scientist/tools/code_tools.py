"""Explicitly unavailable arbitrary-code interface.

Competition execution uses typed operations in ``ExecutionAdapter`` instead.
"""


class CodeTool:
    """Deprecated safety boundary retained for import compatibility."""

    def capabilities(self) -> dict:
        return {"execution_available": False, "deprecated": True, "reason": "arbitrary code execution is not supported"}

    def execute(self, task: dict) -> dict:
        return {
            "status": "rejected",
            "operation": "arbitrary_code",
            "failure_reason": "Arbitrary code execution is not supported; use a whitelisted structured operation.",
            "artifacts": [],
        }
