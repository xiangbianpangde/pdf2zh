"""Failure taxonomy shared by every stage.

Anything raised on purpose derives from :class:`Pdf2mdError`, and each class owns
the exit code the CLI reports. Bugs are *not* wrapped: an unexpected exception
must stay visible instead of being dressed up as a user-facing failure.
"""
from __future__ import annotations

__all__ = [
    "ConfigError",
    "DeliveryError",
    "ImageError",
    "OcrError",
    "Pdf2mdError",
    "TranslationError",
    "UsageError",
]


class Pdf2mdError(RuntimeError):
    """Base class for expected failures. A partial delivery is not a delivery."""

    exit_code = 1


class UsageError(Pdf2mdError):
    """The request is invalid: missing file, bad number, colliding names."""

    exit_code = 2


class ConfigError(Pdf2mdError):
    """Credentials or endpoints are insufficient to start a stage."""

    exit_code = 2


class OcrError(Pdf2mdError):
    """Parsing failed at submit, poll, download or unpack time."""

    def __init__(self, message: str, code: int | str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ImageError(Pdf2mdError):
    """An asset cannot be used as a delivery figure."""


class DeliveryError(Pdf2mdError):
    """Paths, assembly or the delivery contract failed a hard check."""


class TranslationError(Pdf2mdError):
    """The translation endpoint returned unusable output."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
