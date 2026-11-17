"""Tests for the standalone debug script's failure classification."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from py_nationalgrid import CannotConnectError, GraphQLError

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "debug_nationalgrid.py"


def _load_script() -> ModuleType:
    """Load scripts/debug_nationalgrid.py without running main()."""
    spec = importlib.util.spec_from_file_location("debug_nationalgrid", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_error_detail_keeps_status_and_redacts() -> None:
    """Error details carry the HTTP status but no email addresses."""
    script = _load_script()

    detail = script._error_detail(
        "get_linked_accounts",
        GraphQLError("failed for someone@example.com", endpoint="e", status=401),
        0.4,
    )

    assert detail["type"] == "GraphQLError"
    assert detail["status"] == 401
    assert detail["step"] == "get_linked_accounts"
    assert "someone@example.com" not in detail["message"]
    assert "<email>" in detail["message"]


def test_diagnosis_flags_invalid_credentials() -> None:
    """A rejected password is explained as such."""
    script = _load_script()

    diagnosis = script._diagnosis(
        [{"type": "InvalidAuthError", "message": "bad credentials"}]
    )

    assert diagnosis is not None
    assert "multi-factor" in diagnosis


def test_diagnosis_flags_incomplete_auth_flow() -> None:
    """A sign-in without a token is reported as likely MFA."""
    script = _load_script()

    diagnosis = script._diagnosis(
        [
            {
                "type": "CannotConnectError",
                "message": "Failed to obtain authorization code",
            }
        ]
    )

    assert diagnosis is not None
    assert "multi-factor authentication" in diagnosis


def test_diagnosis_flags_b2c_exception() -> None:
    """A rejected B2C session is an auth problem, not a network one."""
    script = _load_script()

    diagnosis = script._diagnosis(
        [
            {
                "type": "CannotConnectError",
                "message": "Authentication failed: AADB2C: An exception has occurred.",
            }
        ]
    )

    assert diagnosis is not None
    assert "multi-factor authentication" in diagnosis


def test_diagnosis_flags_network_failure() -> None:
    """A plain connection failure is not blamed on credentials."""
    script = _load_script()

    diagnosis = script._diagnosis(
        [{"type": CannotConnectError.__name__, "message": "offline"}]
    )

    assert diagnosis is not None
    assert "could not be reached" in diagnosis


def test_no_diagnosis_without_errors() -> None:
    """A clean run reports no diagnosis."""
    script = _load_script()

    assert script._diagnosis([]) is None
