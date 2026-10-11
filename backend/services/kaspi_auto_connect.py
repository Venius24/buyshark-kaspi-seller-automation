"""One-click store connection — WITHHELD in the public version.

The private version, given the owner's phone+SMS session, lists the owner's merchant
accounts, creates a dedicated BuyShark employee in each cabinet with a fixed set of
permissions, and collects the store's Shop API token — so a seller connects a store
without copying tokens by hand. It drives the Kaspi cabinet UI in a browser.
"""
from backend.services._withheld import WithheldError


class AutoConnectError(Exception):
    pass


def start_owner_sms_login(session_key: str, phone: str):
    raise AutoConnectError(str(WithheldError("auto-connect")))


def submit_owner_sms_code(session_key: str, code: str):
    raise AutoConnectError(str(WithheldError("auto-connect")))


def list_owner_merchants(owner_cookies: dict) -> list[str]:
    raise AutoConnectError(str(WithheldError("auto-connect")))


def setup_merchant(owner_cookies: dict, merchant_id: str, employee_email: str, on_step=None) -> dict:
    raise AutoConnectError(str(WithheldError("auto-connect")))
