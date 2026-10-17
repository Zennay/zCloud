"""Shared, redacted source exception type across CLI and imported modules.

When launched via `python -m scripts.zcloud_github_activity`, Python gives the
entry point the name __main__. Dependent modules may also import the canonical
module name. Their errors must still be caught by the entry point so private
file paths and internal tracebacks never leak to an operator console.
"""


class ActivitySourceError(Exception):
    """Sanitized, bounded activity source failure; never include raw secrets."""
