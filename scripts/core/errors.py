class ProjectCoreError(Exception):
    """Base error for deterministic project utilities."""

    code = "project_core_error"
    exit_code = 1

    def __init__(self, message: str, *, details=None):
        super().__init__(message)
        self.message = message
        self.details = details


class VisualContextBudgetError(ProjectCoreError):
    """交付文本携带图片载荷，违反上下文预算红线（data:image / image_url / base64）。"""

    code = "visual_context_budget_error"
    exit_code = 3


class StageContextError(ValueError):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code
