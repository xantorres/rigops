"""The finding ids and statuses that decide doctor's exit code.

An id is a hash of ``(check, key)``, so the contract is the pair. A ``*`` ends a
key whose tail names the defect (a path, a server) and a ``*`` check matches the
driver itself. Renaming an entry breaks every backlog line deduplicated on it.
"""

from __future__ import annotations

EXIT_1_FINDINGS = frozenset({
    ("agents", "read_only_edit:*"),
    ("budget", "always_on"),
    ("jobs", "program:*"),
    ("mcp", "stdio:*"),
    ("plugin_cache_clean", "stale:*"),
    ("pointers", "agent:*"),
    ("pointers", "link:*"),
    ("pointers", "launchd:*"),
    ("pointers", "mcp:*"),
    ("pointers", "model:*"),
    ("pointers", "path:*"),
    ("pointers", "plugin:*"),
    ("pointers", "root:*"),
    ("pointers", "skill:*"),
    ("pointers", "source:*"),
})

EXIT_2_FINDINGS = frozenset({
    ("*", "crashed"),
    ("*", "import"),
    ("budget", "unreadable"),
    ("jobs", "unreadable:*"),
})

EXIT_1_STATUSES = ("failing", "hung", "fail")
