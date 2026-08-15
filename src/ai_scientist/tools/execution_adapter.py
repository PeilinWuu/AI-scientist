"""Execution boundary for future laboratory, compute, or analysis backends."""


class ExecutionAdapter:
    """Declare that no real execution backend is connected in this release."""

    def capabilities(self) -> dict:
        return {"execution_available": False, "backends": []}

    def execute(self, task: dict) -> dict:
        raise NotImplementedError("No execution backend is currently connected.")
