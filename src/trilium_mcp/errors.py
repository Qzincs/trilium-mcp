"""Safe, user-facing errors for Trilium ETAPI failures."""


class TriliumError(Exception):
    """Base error exposed to MCP callers."""


class TriliumConnectionError(TriliumError):
    """Trilium could not be reached."""


class TriliumAuthenticationError(TriliumError):
    """The ETAPI token was rejected."""


class TriliumNotFoundError(TriliumError):
    """The requested Trilium resource does not exist."""


class TriliumConflictError(TriliumError):
    """A note changed after the caller last read it."""


class TriliumWriteConfirmationRequired(TriliumError):
    """A write was requested without explicit confirmation."""


class TriliumRateLimitError(TriliumError):
    """Trilium rejected the request due to rate limiting."""


class TriliumAPIError(TriliumError):
    """Trilium returned an unexpected response."""
