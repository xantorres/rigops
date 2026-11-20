from __future__ import annotations


class RigopsError(Exception):
    """A failure the command cannot judge past; the shared runner maps it to exit 2."""
