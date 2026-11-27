import sys

_PEER_MODULE = sys.modules.get(
    "core.errors" if __name__ == "scripts.core.errors" else "scripts.core.errors"
)

if _PEER_MODULE is not None:
    StageContextError = _PEER_MODULE.StageContextError
    RevisionControlError = _PEER_MODULE.RevisionControlError
else:
    class StageContextError(ValueError):
        def __init__(self, message, *, code=None):
            super().__init__(message)
            self.code = code

    class RevisionControlError(ValueError):
        """A revision request cannot safely advance."""

