"""Arithmetic shared by the card and the nudge: tokens are bytes over four."""

from __future__ import annotations


def tokens(text: str) -> int:
    return len((text or "").encode()) // 4


def clip(text: str, max_tokens: int) -> str:
    """The longest prefix within `max_tokens`, never cutting a character in two."""
    return (text or "").encode()[:max_tokens * 4].decode("utf-8", "ignore")


__all__ = ["tokens", "clip"]
