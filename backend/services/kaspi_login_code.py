"""Kaspi cabinet one-time login codes delivered by email — WITHHELD in the public version.

The private version gives each connected store a mailbox on our own domain; Kaspi's
login-code and welcome emails land on an inbound-email webhook, and the login flow
polls for the code/temporary password that arrived after the attempt started.
"""
from datetime import datetime, timezone

from backend.services._withheld import WithheldError

CODE_SCREEN_MARKER = ""
CODE_WAIT_SECONDS = 90


def is_our_mailbox(email: str) -> bool:
    return False


def extract_login_code(raw_mime: str):
    return None


def extract_temp_password(raw_mime: str):
    return None


def wait_for_login_code(email: str, since: datetime, timeout: float = CODE_WAIT_SECONDS):
    raise WithheldError("email login code")


def wait_for_temp_password(email: str, since: datetime, timeout: float = CODE_WAIT_SECONDS):
    raise WithheldError("email temporary password")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
