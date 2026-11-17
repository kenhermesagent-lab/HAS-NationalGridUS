#!/usr/bin/env python3
"""Dump National Grid API responses with secrets redacted.

Use this when setup fails on a real account: it signs in with the same
py-nationalgrid client the integration uses, then dumps the account, meter,
AMI usage and interval payloads it gets back, plus the library's login log and
the error details.

    python scripts/debug_nationalgrid.py --days 7 --output ng-debug.json

Credentials are read from --username/--password, the NG_USERNAME/NG_PASSWORD
environment variables, or an interactive prompt. They are never written to the
output. Emails, passwords and tokens are always redacted; account, meter,
premise and service point numbers are masked unless --show-ids is passed.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from py_nationalgrid import (
    NationalGridClient,
    NationalGridConfig,
    create_cookie_jar,
)

from custom_components.national_grid_us.redact import (
    REDACTION_VERSION,
    redact_payload,
    redact_text,
)

UTILITY_TIME_ZONE = "America/New_York"

_DIAGNOSES = {
    "invalid_auth": (
        "National Grid rejected the email and password. Check them at "
        "https://myaccount.nationalgrid.com - if the site accepts them, the "
        "account most likely requires multi-factor authentication."
    ),
    "auth_flow": (
        "The sign-in flow finished without a token. This normally means the "
        "account requires interactive multi-factor authentication, which the "
        "integration cannot complete."
    ),
    "graphql": (
        "Sign-in appeared to work but the API rejected the request; see the "
        "status code in the errors section."
    ),
    "cannot_connect": (
        "The National Grid servers could not be reached or returned an "
        "unexpected response; see the errors section."
    ),
}

# py-nationalgrid reports a refused Azure AD B2C session as CannotConnectError.
_B2C_MARKERS = ("authorization code", "access token", "aadb2c", "b2c_exception")


class _LogCapture(logging.Handler):
    """Collect py-nationalgrid log records for the dump."""

    def __init__(self, *, mask_ids: bool = True) -> None:
        super().__init__(level=logging.DEBUG)
        self._mask_ids = mask_ids
        self.records: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(
            {
                "level": record.levelname,
                "message": redact_text(record.getMessage(), mask_ids=self._mask_ids),
            }
        )


def _error_detail(
    step: str, err: Exception, duration: float, *, mask_ids: bool = True
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "step": step,
        "type": type(err).__name__,
        "message": redact_text(str(err), mask_ids=mask_ids),
        "duration_s": round(duration, 2),
    }
    status = getattr(err, "status", None)
    if status is not None:
        detail["status"] = status
    return detail


async def _step(
    name: str, coroutine: Any, errors: list[dict[str, Any]], *, mask_ids: bool = True
) -> Any | None:
    """Await a client call, recording failures instead of aborting."""
    started = time.monotonic()
    try:
        result = await coroutine
    except Exception as err:  # noqa: BLE001 - the dump must survive any failure
        errors.append(
            _error_detail(name, err, time.monotonic() - started, mask_ids=mask_ids)
        )
        print(f"  {name}: FAILED ({type(err).__name__})", file=sys.stderr)
        return None
    duration = round(time.monotonic() - started, 2)
    count = f" ({len(result)} records)" if isinstance(result, list) else ""
    print(f"  {name}: ok{count} [{duration}s]", file=sys.stderr)
    return result


def _diagnosis(errors: list[dict[str, Any]]) -> str | None:
    """Explain the most likely cause of a failure."""
    for error in errors:
        if error["type"] == "InvalidAuthError":
            return _DIAGNOSES["invalid_auth"]
        if error["type"] == "CannotConnectError" and any(
            marker in error["message"].lower() for marker in _B2C_MARKERS
        ):
            return _DIAGNOSES["auth_flow"]
    for error in errors:
        if error["type"] == "GraphQLError":
            return _DIAGNOSES["graphql"]
        if error["type"] == "CannotConnectError":
            return _DIAGNOSES["cannot_connect"]
    return None


async def collect(
    username: str,
    password: str,
    *,
    days: int,
    account_filter: str | None,
    intervals: bool,
    mask_ids: bool = True,
) -> dict[str, Any]:
    """Sign in and collect every response the integration depends on."""
    capture = _LogCapture(mask_ids=mask_ids)
    package_logger = logging.getLogger("py_nationalgrid")
    package_logger.setLevel(logging.DEBUG)
    package_logger.addHandler(capture)

    errors: list[dict[str, Any]] = []
    data: dict[str, Any] = {
        "linked_accounts": None,
        "billing_accounts": {},
        "ami_usage": {},
        "interval_reads": {},
    }
    login_ok = False

    try:
        async with aiohttp.ClientSession(
            cookie_jar=create_cookie_jar(),
            timeout=aiohttp.ClientTimeout(total=90),
        ) as session:
            client = NationalGridClient(
                config=NationalGridConfig(username=username, password=password),
                session=session,
            )

            print("Signing in and reading accounts...", file=sys.stderr)
            links = await _step(
                "get_linked_accounts",
                client.get_linked_accounts(),
                errors,
                mask_ids=mask_ids,
            )
            data["linked_accounts"] = links
            if links is None:
                return _payload(data, errors, capture, login_ok=False)

            login_ok = True
            accounts = sorted(
                {
                    str(link["billingAccountId"])
                    for link in links
                    if link.get("billingAccountId")
                }
            )
            if account_filter:
                accounts = [
                    account for account in accounts if account == account_filter
                ]

            today = datetime.now(ZoneInfo(UTILITY_TIME_ZONE)).date()
            date_from = today - timedelta(days=days)

            for account in accounts:
                billing = await _step(
                    f"get_billing_account({account})",
                    client.get_billing_account(account),
                    errors,
                    mask_ids=mask_ids,
                )
                data["billing_accounts"][account] = billing
                if not billing:
                    continue

                premise = billing.get("premiseNumber", "")
                for meter in billing.get("meter", {}).get("nodes", []):
                    service_point = meter.get("servicePointNumber")
                    if not service_point:
                        continue
                    meter_key = f"{account}/{service_point}"
                    fuel_type = str(meter.get("fuelType") or "ELECTRIC").upper()

                    data["ami_usage"][meter_key] = await _step(
                        f"get_ami_energy_usages({meter_key})",
                        client.get_ami_energy_usages(
                            meter_number=str(meter.get("meterNumber") or ""),
                            premise_number=premise,
                            service_point_number=service_point,
                            meter_point_number=meter.get("meterPointNumber") or "",
                            date_from=date_from,
                            date_to=today,
                            fuel_type=fuel_type,
                        ),
                        errors,
                        mask_ids=mask_ids,
                    )

                    if intervals and fuel_type == "ELECTRIC":
                        data["interval_reads"][meter_key] = await _step(
                            f"get_interval_reads({meter_key})",
                            client.get_interval_reads(
                                premise_number=premise,
                                service_point_number=service_point,
                                start_datetime=datetime.combine(
                                    today - timedelta(days=2),
                                    datetime.min.time(),
                                ),
                            ),
                            errors,
                            mask_ids=mask_ids,
                        )
    finally:
        package_logger.removeHandler(capture)

    return _payload(data, errors, capture, login_ok=login_ok)


def _payload(
    data: dict[str, Any],
    errors: list[dict[str, Any]],
    capture: _LogCapture,
    *,
    login_ok: bool,
) -> dict[str, Any]:
    try:
        from importlib.metadata import version

        package_version = version("py-nationalgrid")
    except Exception:  # noqa: BLE001 - version is informational only
        package_version = "unknown"

    return {
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(),
            "python": sys.version.split()[0],
            "py_nationalgrid": package_version,
            "redaction_version": REDACTION_VERSION,
            "login_ok": login_ok,
        },
        "diagnosis": _diagnosis(errors),
        "login_log": capture.records,
        "errors": errors,
        "data": data,
    }


def main() -> int:
    """Run the dump and print or write the redacted JSON."""
    parser = argparse.ArgumentParser(
        description="Dump National Grid API responses with secrets redacted."
    )
    parser.add_argument("--username", default=os.environ.get("NG_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("NG_PASSWORD"))
    parser.add_argument(
        "--days", type=int, default=7, help="Days of AMI usage to request (default 7)"
    )
    parser.add_argument(
        "--account", default=None, help="Only inspect this billing account number"
    )
    parser.add_argument(
        "--no-intervals", action="store_true", help="Skip 15-minute interval reads"
    )
    parser.add_argument(
        "--show-ids",
        action="store_true",
        help="Keep account, meter, premise and service point numbers unmasked",
    )
    parser.add_argument("--output", help="Write the JSON dump to this file")
    args = parser.parse_args()

    username = args.username or input("National Grid email: ").strip()
    if not username:
        print("A username is required.", file=sys.stderr)
        return 64
    password = args.password or getpass.getpass("National Grid password: ")
    mask_ids = not args.show_ids

    payload = asyncio.run(
        collect(
            username,
            password,
            days=args.days,
            account_filter=args.account,
            intervals=not args.no_intervals,
            mask_ids=mask_ids,
        )
    )
    payload = redact_payload(payload, mask_ids=mask_ids)

    text = json.dumps(payload, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(text)

    if payload["diagnosis"]:
        print(f"\nDiagnosis: {payload['diagnosis']}", file=sys.stderr)
    print(
        "\nEmails, passwords and tokens are redacted"
        + (
            ""
            if args.show_ids
            else "; identifiers are masked with --show-ids to reveal"
        )
        + ".",
        file=sys.stderr,
    )
    return 0 if payload["meta"]["login_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
