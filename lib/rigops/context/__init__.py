"""What a session is told at the two moments it can still act on it.

``card`` renders the SessionStart summary: which realm and scopes a cwd
prefetches from, any doctor gate still tripped, and whether the always-loaded
surface is over its budget. ``nudge`` renders the UserPromptSubmit reminders
and prompt-scoped prefetch. Both hold pure logic only -- the registry, the
retrieval index and the retrieval log are wired in by the libexec scripts that
call them, per the package import rule (``rigops.core`` and nothing else from
``rigops``).
"""

from __future__ import annotations

__all__ = ["card", "nudge", "util"]
