"""Exception hierarchy for the hub protocol."""

from __future__ import annotations


class GarageError(Exception):
    """Base class for every protocol error."""


class HubUnreachableError(GarageError):
    """The hub (or the vendor cloud, during pairing) could not be reached."""


class AuthenticationError(GarageError):
    """The hub or cloud rejected the credentials presented to it."""


class PairingError(GarageError):
    """The one-time pairing handshake failed."""


class HubCommandError(GarageError):
    """The hub returned an explicit error for a command.

    A common cause is the hub's *phone lockout* being on, which blocks every
    app-protocol command (status reads keep working).
    """

    def __init__(self, code: int | str, message: str) -> None:
        super().__init__(f"hub rejected command ({code}): {message}")
        self.code = code
        self.message = message
