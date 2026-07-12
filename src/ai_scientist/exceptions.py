"""AI Scientist-specific exceptions."""


class AIScientistError(RuntimeError):
    """Base error for research workflow failures."""


class ProjectNotFoundError(AIScientistError):
    """Raised when a persisted project does not exist."""


class InvalidTransitionError(AIScientistError):
    """Raised when a state-machine transition is not allowed."""


class SkillValidationError(AIScientistError):
    """Raised when a skill file is missing or invalid."""


class StructuredOutputError(AIScientistError):
    """Raised after structured model output cannot be validated."""


class BudgetExceededError(AIScientistError):
    """Raised when the configured research budget is exhausted."""


class ModelConfigurationError(AIScientistError):
    """Raised before provider calls when no valid model can be resolved."""
