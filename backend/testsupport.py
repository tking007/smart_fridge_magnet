"""Helpers shared by backend tests."""

import secrets


def disposable_token(prefix: str) -> str:
    """Return a random, process-local credential for a test."""
    return prefix + secrets.token_urlsafe(24)
