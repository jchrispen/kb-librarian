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


class DuplicateNoteIdError(KBLibrarianError):
    """Raised when the same note ID exists in more than one file."""


class SearchIndexError(KBLibrarianError):
    """Raised when the local lexical index is missing or invalid."""


class NoteNotFoundError(KBLibrarianError):
    """Raised when a requested note ID cannot be found."""


class AmbiguousNoteIdError(KBLibrarianError):
    """Raised when a requested note ID maps to more than one note file."""


class ProviderError(KBLibrarianError):
    """Raised when provider configuration or responses are invalid."""


class IngestError(KBLibrarianError):
    """Raised when ingest cannot proceed safely."""
