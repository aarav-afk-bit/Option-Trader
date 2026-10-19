"""Exceptions shared across the app."""


class OptionTraderError(Exception):
    """Base class for app errors."""


class DataUnavailableError(OptionTraderError):
    """Raised when real market data cannot be obtained.

    The app never substitutes synthetic data; callers surface this error to the user instead.
    """


class ProviderNotConfiguredError(DataUnavailableError):
    """A provider needs credentials or settings that have not been supplied."""


class InvalidDataError(DataUnavailableError):
    """A source responded, but the payload failed validation (empty, stale or malformed)."""
