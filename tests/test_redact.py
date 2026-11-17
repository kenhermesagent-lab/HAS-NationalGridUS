"""Tests for the redaction helpers used by diagnostics and the debug script."""

from __future__ import annotations

from typing import Any

from custom_components.national_grid_us.redact import (
    mask_identifier,
    mask_numbers,
    redact_payload,
    redact_text,
)


def test_payload_keeps_dates_and_values() -> None:
    """Redaction must not destroy dates or usage numbers."""
    payload: dict[str, Any] = {
        "date": "2026-11-15",
        "quantity": 12.5,
        "billingAccountId": "1234567890",
    }

    redacted = redact_payload(payload)

    assert redacted["date"] == "2026-11-15"
    assert redacted["quantity"] == 12.5
    assert redacted["billingAccountId"].startswith("id:")
    assert "1234567890" not in redacted["billingAccountId"]


def test_payload_hides_secrets_recursively() -> None:
    """Secret keys are replaced wherever they appear."""
    redacted = redact_payload(
        {
            "password": "hunter2",
            "username": "someone@example.com",
            "nested": {"access_token": "abc", "items": [{"cookie": "session=1"}]},
        }
    )

    assert redacted["password"] == "<redacted>"
    assert redacted["username"] == "<redacted>"
    assert redacted["nested"]["access_token"] == "<redacted>"
    assert redacted["nested"]["items"][0]["cookie"] == "<redacted>"


def test_text_removes_emails_and_tokens() -> None:
    """Free-form text loses email addresses and bearer tokens."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrst"

    assert redact_text("login failed for someone@example.com") == (
        "login failed for <email>"
    )
    assert "<jwt>" in redact_text(f"Authorization: Bearer {jwt}")


def test_mask_identifier_is_stable_and_opaque() -> None:
    """The same identifier always maps to the same placeholder."""
    first = mask_identifier("1234567890")
    assert first == mask_identifier("1234567890")
    assert first != mask_identifier("9876543210")
    assert "1234567890" not in first


def test_text_masks_identifiers_in_urls_and_errors() -> None:
    """Long identifiers in free text go, while dates and readings stay."""
    text = (
        "GET https://myaccount.nationalgrid.com/api/billing/1234567890/usage"
        "?servicePointNumber=987654321 failed: account 1234567890 unknown; "
        "reading 16.0 kWh at 0.1234 per kWh on 2026-11-15 at "
        "2026-11-15T04:50:10.123456+00:00 (total 1234567.89 kWh)"
    )

    redacted = redact_text(text)

    assert "1234567890" not in redacted
    assert "987654321" not in redacted
    assert "/id:" in redacted
    assert "16.0 kWh" in redacted
    assert "0.1234 per kWh" in redacted
    assert "2026-11-15T04:50:10.123456+00:00" in redacted
    assert "1234567.89 kWh" in redacted


def test_text_masks_identifiers_glued_to_punctuation() -> None:
    """billing-1234567890 is masked, while ISO dates and timestamps survive."""
    text = (
        "billing-1234567890, meter+987654321, premise~555000111, "
        "on 2026-11-15 at 2026-11-15T04:50:10.123456+00:00 "
        "(clock 10:30:00.123456), use 16.0 kWh at 0.1234/kWh, total 1234567.89 kWh"
    )

    redacted = redact_text(text)

    assert "1234567890" not in redacted
    assert "987654321" not in redacted
    assert "555000111" not in redacted
    assert redacted.count("id:") == 3
    assert "2026-11-15 at " in redacted
    assert "2026-11-15T04:50:10.123456+00:00" in redacted
    assert "10:30:00.123456" in redacted
    assert "16.0 kWh at 0.1234/kWh" in redacted
    assert "1234567.89 kWh" in redacted


def test_text_masks_short_identifiers_like_payload_values() -> None:
    """Three to five digit premise and service point numbers are masked too."""
    text = (
        "premise 555, service point 777, meter M-1, account 1234567890, "
        "error AADB2C90255"
    )

    redacted = redact_text(text)

    assert "555" not in redacted
    assert "777" not in redacted
    assert "1234567890" not in redacted
    assert redacted.count("id:") == 3
    # Same placeholders as the payload masking, so text and dump agree.
    assert mask_identifier("555") in redacted
    assert mask_identifier("777") in redacted
    assert redact_payload({"premise": "555"})["premise"] == mask_identifier("555")
    # Tokens that merely contain digits are error codes, not identifiers.
    assert "AADB2C90255" in redacted
    assert "M-1" in redacted


def test_text_keeps_identifiers_when_ids_are_shown() -> None:
    """--show-ids keeps identifiers but still removes secrets."""
    text = "account 1234567890 for someone@example.com"

    shown = redact_text(text, mask_ids=False)

    assert "1234567890" in shown
    assert "someone@example.com" not in shown


def test_identifiers_used_as_keys_are_masked() -> None:
    """Account numbers must not survive as dictionary keys."""
    redacted = redact_payload(
        {
            "1234567890": {"balance": 1.0},
            "1234567890/777": [1, 2],
            "2026-11-15": 5.0,
        }
    )

    keys = list(redacted)
    assert all("1234567890" not in key for key in keys)
    assert keys[0].startswith("id:")
    assert keys[1].startswith("id:") and "/id:" in keys[1]
    assert "2026-11-15" in keys


def test_mask_numbers_handles_statistic_ids() -> None:
    """Statistic ids keep their shape but lose the account and service point."""
    masked = mask_numbers("national_grid_us:1234567890_777_electric_usage")

    assert masked.startswith("national_grid_us:id:")
    assert masked.endswith("_electric_usage")
    assert "1234567890" not in masked
    assert "_777_" not in masked
