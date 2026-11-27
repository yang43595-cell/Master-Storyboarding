class StageContextError(ValueError):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code


class RevisionControlError(ValueError):
    """A revision request cannot safely advance."""
