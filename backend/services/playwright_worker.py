"""Browser session manager for the Kaspi seller cabinet — WITHHELD in the public version.

The private version runs Kaspi cabinet logins (email+password, email one-time code,
phone+SMS for the chat) in a bounded pool of short-lived Chromium instances, one per
call, so concurrent logins of different stores never share state; the SMS flow is the
only stateful one (the browser must stay on the same page between "code sent" and
"code entered") and lives in a per-store thread with a TTL.

This stub keeps the interface so the rest of the app imports and runs; every
operation reports that it is unavailable.
"""
import logging

from backend.services._withheld import WITHHELD_MSG, WithheldError

logger = logging.getLogger(__name__)


class Command:
    LOGIN = "login"
    START_SMS_LOGIN = "start_sms_login"
    SUBMIT_SMS_LOGIN = "submit_sms_login"
    VERIFY_SESSION = "verify_session"


def _is_headless() -> bool:
    return True


def _launch_browser(playwright):
    raise WithheldError("browser launch")


class PlaywrightSessionManager:
    def run_in_browser_pool(self, fn, *args, **kwargs):
        raise WithheldError("browser pool")

    def send_cmd(self, cmd, data=None, store_id: str = None):
        """Same contract as the private version: (ok, message, cookies)."""
        return False, WITHHELD_MSG, {}


worker = PlaywrightSessionManager()
