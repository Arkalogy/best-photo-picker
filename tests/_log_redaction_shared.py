"""Shared fixtures and helpers of the test_log_redaction.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

# A representative 64-hex token (matches secrets.token_hex(32) shape)
_TOKEN = "0123456789abcdef" * 4
