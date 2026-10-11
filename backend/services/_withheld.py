"""Shared marker for modules withheld from the public version (see README)."""

WITHHELD_MSG = (
    "Not available in the public version: this module automated access to Kaspi.kz "
    "internals and is intentionally withheld. See README, section 'What is not published'."
)


class WithheldError(RuntimeError):
    def __init__(self, what: str = ""):
        super().__init__(f"{what}: {WITHHELD_MSG}" if what else WITHHELD_MSG)
