class ProjectCoreError(Exception):
    """Base error for deterministic project utilities."""

    code = "project_core_error"
    exit_code = 1

    def __init__(self, message: str, *, details=None):
        super().__init__(message)
        self.message = message
        self.details = details


class RequestValidationError(ProjectCoreError):
    code = "request_validation_error"
    exit_code = 2


class ConflictError(ProjectCoreError):
    code = "conflict"
    exit_code = 4


class KnowledgeContextError(ProjectCoreError):
    """Dynamic knowledge discovery failed for a stage package."""

    code = "knowledge_context_error"
    exit_code = 7


class VisualContextBudgetError(ProjectCoreError):
    """交付文本携带图片载荷，违反上下文预算红线（data:image / image_url / base64）。"""

    code = "visual_context_budget_error"
    exit_code = 3

