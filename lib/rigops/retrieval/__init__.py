"""One retrieval surface across every store the rig knows about.

The index is section-granular and every row carries the realm that owns it, so
a query can never return a row from a realm the caller is not in. Realm comes
from the roots registry at index time, not from the query, because a filter the
caller chooses is a filter the caller can forget.

Modules: ``roots`` (registry and realm resolution), ``chunk`` (section
splitter), ``index`` (walker and writer), ``search`` (query, budget, prefetch),
``log`` (retrieval log and its statistics).
"""

from __future__ import annotations

__all__ = ["roots", "chunk", "index", "search", "log"]
