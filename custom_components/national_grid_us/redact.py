"""Redaction helpers shared by diagnostics and the debug script.

This module deliberately imports nothing from Home Assistant so the standalone
debug script can reuse it.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_JWT_RE = re.compile(r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}")
# Identifier values (account, meter, premise, service point) are all-digit
# strings; dates such as 2026-11-15 must never be mistaken for one.
_WHOLE_ID_RE = re.compile(r"\d{3,}")
_INLINE_ID_RE = re.compile(r"\d{3,}")
# The same identifiers also show up inside URLs and API error strings, glued to
# punctuation such as billing-1234567890. Numbers that must survive are matched
# first - ISO dates and timestamps, bare clock times and decimal readings - and
# what is left is treated as an identifier using the same threshold as
# redact_payload() uses for whole values: three or more digits. Runs attached to
# letters or digits, such as the AADB2C90255 error code, are not identifiers.
# The outer group is required: re.split() drops the matched text unless the
# pattern captures it, and these numbers must survive the split.
_KEEP_NUMERIC_RE = re.compile(
    "("
    r"\d{4}-\d{2}-\d{2}"  # ISO date
    r"(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"  # timestamp
    r"|\d{2}:\d{2}:\d{2}(?:\.\d+)?"  # bare clock time
    r"|\d+\.\d+"  # decimal reading
    ")"
)
_IDENTIFIER_RUN_RE = re.compile(r"(?<![A-Za-z0-9])\d{3,}(?![A-Za-z0-9])")

# Bumped whenever the shape of a redacted dump changes.
REDACTION_VERSION = 4

SECRET_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "cookie",
        "id_token",
        "password",
        "passwd",
        "refresh_token",
        "secret",
        "set-cookie",
        "token",
        "unique_id",
        "username",
    }
)

REDACTED = "<redacted>"


def mask_identifier(value: object) -> str:
    """Return a stable, non-reversible placeholder for an identifier.

    The same value always yields the same token, so a dump still shows which
    readings belong to which meter without revealing the account number.
    """
    digest = hashlib.sha256(str(value).encode()).hexdigest()[:8]
    return f"id:{digest}"


def _mask_identifiers(text: str) -> str:
    """Mask long digit runs, leaving dates, times and readings alone."""
    pieces = _KEEP_NUMERIC_RE.split(text)
    # The captured protected numbers land on the odd indexes, so only the gaps
    # between them are searched for identifiers.
    for index in range(0, len(pieces), 2):
        pieces[index] = _IDENTIFIER_RUN_RE.sub(
            lambda match: mask_identifier(match.group()), pieces[index]
        )
    return "".join(pieces)


def redact_text(value: str, *, mask_ids: bool = True) -> str:
    """Remove email addresses, bearer tokens and identifiers from free text."""
    text = _EMAIL_RE.sub("<email>", value)
    text = _JWT_RE.sub("<jwt>", text)
    if not mask_ids:
        return text
    return _mask_identifiers(text)


def mask_numbers(text: str) -> str:
    """Mask identifier numbers embedded in a longer string (statistic ids)."""
    return _INLINE_ID_RE.sub(lambda match: mask_identifier(match.group()), text)


def _redact_key(key: str) -> str:
    """Mask an identifier used as a dictionary key (``123`` or ``123/456``)."""
    parts = key.split("/")
    if len(parts) > 1 and all(part.isdigit() for part in parts):
        return "/".join(mask_identifier(part) for part in parts)
    if key.isdigit() and len(key) >= 3:
        return mask_identifier(key)
    return key


def redact_payload(value: Any, *, mask_ids: bool = True) -> Any:
    """Return a deep copy of ``value`` with secrets and identifiers removed."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if text_key.lower() in SECRET_KEYS:
                redacted[text_key] = REDACTED
                continue
            out_key = _redact_key(text_key) if mask_ids else text_key
            redacted[out_key] = redact_payload(item, mask_ids=mask_ids)
        return redacted

    if isinstance(value, (list, tuple)):
        return [redact_payload(item, mask_ids=mask_ids) for item in value]

    if isinstance(value, str):
        if mask_ids and _WHOLE_ID_RE.fullmatch(value):
            return mask_identifier(value)
        return redact_text(value)

    return value
