"""Arithmetic shared by the card and the nudge: tokens are bytes over four."""

from __future__ import annotations


def tokens(text: str) -> int:
    return len(text or "") // 4


__all__ = ["tokens"]
