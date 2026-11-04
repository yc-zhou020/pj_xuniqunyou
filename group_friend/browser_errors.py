class BrowserUIError(RuntimeError):
    """Fixed local diagnostic code; never an upstream exception body."""


class ManualActionRequired(BrowserUIError):
    """Login/verification must be performed by the user."""
