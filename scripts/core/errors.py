class StageContextError(ValueError):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code
