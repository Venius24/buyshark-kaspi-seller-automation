"""First message to a buyer through the order's chat widget — WITHHELD in the public version.

The private version opens the order in the seller cabinet and writes the first chat
message (used by the "pickup reminder" and "leave a review" campaigns) when the chat
API alone cannot start a conversation.
"""
from backend.services._withheld import WITHHELD_MSG

STATUS_BY_CAMPAIGN = {
    "pickup": "PICKUP",
    "review": "ARCHIVED",
}


def send_first_contact(cookies: dict, order_id: str, message_text: str, status: str = "PICKUP"):
    """Same contract as the private version: (ok, message)."""
    return False, WITHHELD_MSG
