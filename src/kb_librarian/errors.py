"""Domain exceptions for KB Librarian."""


class KBLibrarianError(Exception):
    """Base exception for actionable CLI errors."""


class ConfigError(KBLibrarianError):
    """Raised when configuration cannot be loaded or validated."""


class NoteValidationError(KBLibrarianError):
    """Raised when a markdown note does not satisfy the note schema."""


class NoteParseError(KBLibrarianError):
    """Raised when a markdown note cannot be parsed."""


class MilestoneNotImplementedError(KBLibrarianError):
    """Raised for commands intentionally deferred beyond the current milestone."""
