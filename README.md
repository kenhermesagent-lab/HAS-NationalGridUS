# National Grid US for Home Assistant

A custom integration that pulls **hourly, daily, weekly and monthly** electricity
and gas usage from National Grid US accounts into Home Assistant, and imports it
as long-term statistics so the Energy Dashboard and the statistics graphs work.

## Why the Opower integration stopped working

Home Assistant's core [Opower](https://www.home-assistant.io/integrations/opower)
integration was the supported way to get National Grid US usage. National Grid
moved off Oracle Opower at the end of 2025 and now serves usage from its own
GraphQL API (`myaccount.nationalgrid.com`), so the Opower integration returns
errors such as:

```
HTTP Error: 500 URL: ngny-gas.opower.com/ei/edge/apis/multi-account-v1/cws/ngbk/customers
{"error":"Unable to obtain a JWT: Astro error with a response 503"}
```

This integration talks to the current National Grid API instead, using the
maintained [py-nationalgrid](https://github.com/virtitnerd/py-nationalgrid)
library.

## Features

* Hourly usage from 15-minute AMI interval reads (electric meters)
* Daily usage from settled AMI readings (electric and gas)
* Weekly and monthly totals as sensors, plus every period in statistics graphs
* Long-term statistics per meter, compatible with the Energy Dashboard
* Returns/exports (solar) kept in a separate return-to-grid series
* Re-authentication and account re-selection from the UI

## Requirements

- Home Assistant **2025.2** or newer (Python 3.13), because
  `py-nationalgrid` 0.6.8 requires Python 3.13
- The `recorder` integration enabled, for long-term statistics
- A National Grid login that can sign in **without interactive MFA** (see
  [Login fails even though the website works](#login-fails-even-though-the-website-works))

## Installation

### HACS

1. HACS → Integrations → three-dot menu → **Custom repositories**
2. Add `https://github.com/kenhermesagent-lab/HAS-NationalGridUS` as **Integration**
3. Install **National Grid US** and restart Home Assistant

### Manual

Copy `custom_components/national_grid_us` into your Home Assistant `config/custom_components/`
directory and restart.

## Setup

1. **Settings → Devices & Services → Add Integration → National Grid US**
2. Enter the email and password you use at
   [myaccount.nationalgrid.com](https://myaccount.nationalgrid.com)
3. Select the billing accounts to monitor

Accounts can be changed later with **Configure** on the integration entry.
The integration polls every 30 minutes at most.

## Entities

Each meter becomes a device with these sensors:

| Entity | Period | Unit |
| --- | --- | --- |
| Usage this hour | Current clock hour | kWh / CCF |
| Usage today | Local midnight → now | kWh / CCF |
| Usage yesterday | Previous local day | kWh / CCF |
| Usage this week | Monday → now | kWh / CCF |
| Usage this month | 1st of the month → now | kWh / CCF |
| Usage last month | Previous calendar month | kWh / CCF |

Plus a diagnostic **Smart meter** binary sensor and a **Refresh usage** button
that forces an immediate re-fetch.

Gas meters report `CCF`; electricity meters report `kWh`. Both are the units
National Grid's own portal uses.

## Long-term statistics

Usage is imported into statistics, which is what makes the hourly/daily/weekly/
monthly graphs and the Energy Dashboard work. Find the series in
**Settings → Tools → Statistics** by searching for `national_grid_us`:

```
national_grid_us:<account_id>_<service_point>_electric_usage
national_grid_us:<account_id>_<service_point>_electric_return
national_grid_us:<account_id>_<service_point>_gas_usage
```

For example `national_grid_us:1234567890_123456789_electric_usage`. The account
number and service point number are shown on the meter device page.

### Energy Dashboard

1. **Settings → Dashboards → Energy**
2. Under **Electricity grid**, choose your `..._electric_usage` statistic
3. Under **Return to grid**, choose `..._electric_return` if you have solar
4. Under **Gas consumption**, choose `..._gas_usage`

Add each meter's statistics separately. Do not also add the old Opower
statistics for the same meter, or the same usage will be counted twice.

## How the data is built

* **Settled usage** comes from the AMI GraphQL endpoint. National Grid finalizes
  readings with a 1–2 day delay, so the last day or two are often missing here.
  Each settled day is imported as a single one-per-day point, which is enough for
  daily/weekly/monthly totals.
* **Hourly usage** comes from the near real-time interval endpoint, which serves
  roughly the last two days of 15-minute reads. These are summed into hourly
  buckets.
* A day that already has interval coverage is *not* also imported from the
  settled daily total, so nothing is counted twice.
* Statistics are append-only and cumulative: only readings newer than the last
  stored point are written, so past history is never rewritten.

## Limitations

* National Grid keeps only about 45 days of AMI data in fast storage, so a fresh
  install backfills roughly 45 days, not years.
* Gas meters do not expose interval reads; gas is daily or monthly only.
* Accounts using interactive MFA cannot sign in from an integration. Disable MFA
  for the account or use a password-only login if your account offers one.
* Usage days are bucketed in `America/New_York`, the timezone National Grid
  reports in.
* There is no billing or cost data in this integration; it is usage only.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  default: info
  logs:
    custom_components.national_grid_us: debug
    py_nationalgrid: debug
```

If no statistics appear, check that the `recorder` integration is enabled — long
term statistics require it.

### Login fails even though the website works

This is almost always **multi-factor authentication**. You sign in to
`myaccount.nationalgrid.com` interactively, so Azure AD B2C can prompt for a code;
the integration signs in headlessly and cannot answer that prompt. National Grid
reports this as a completed sign-in with no token, which the form shows as
*"sign-in finished without returning a token"*.

National Grid only requires MFA on some accounts. If yours does, the integration
can't read your usage until it is turned off for that login, or National Grid
offers a password-only path.

To confirm what actually happened on your account:

1. **Download diagnostics** (below) — it works even when setup failed, and shows
the exact error.
2. **Run the debug script** (below) — it adds the library's sign-in log and the
raw API responses.

### Downloading diagnostics

**Settings → Devices & Services → National Grid US → ⋮ → Download diagnostics**.

It is redacted: passwords, tokens and emails are removed, and account, meter,
premise and service point numbers are replaced with stable `id:…` hashes (the
same number always gets the same hash, so readings still line up). The download
includes the setup error, coordinator state, per-meter point counts, recent
values, and the last stored statistics row for each series — which is what to
attach to an issue.

### Debug script

`scripts/debug_nationalgrid.py` runs outside Home Assistant with only
`py-nationalgrid` installed, and dumps what the API returns:

```bash
pip install py-nationalgrid==0.6.8
python scripts/debug_nationalgrid.py --days 7 --output ng-debug.json
```

It prints the library's sign-in log, every error with its type/message/HTTP
status, a plain-language diagnosis, and the linked accounts, billing accounts,
AMI usage and 15-minute interval payloads.

| Flag | Effect |
| --- | --- |
| `--days N` | Days of AMI usage to request (default 7) |
| `--account N` | Only inspect that billing account |
| `--no-intervals` | Skip the 15-minute interval request |
| `--show-ids` | Keep account/meter/premise numbers unmasked |
| `--output FILE` | Write JSON to a file instead of stdout |

Credentials come from `--username`/`--password`, `NG_USERNAME`/`NG_PASSWORD`, or
a prompt, and are never written to the dump. Emails, passwords and tokens are
always redacted. Exit code is `0` when the login worked, `2` when it failed.

## Development

Tests run inside Home Assistant's own test harness
([pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component)),
so they exercise the real config-entry manager, entity registry, device registry
and recorder instead of mocks of Home Assistant itself. Only the National Grid
API client is faked (`tests/fake_client.py`).

```bash
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

Covered by `tests/test_config_flow.py`:

* the full add-integration flow (credentials → account picker → entry)
* invalid credentials, connection failures, a login with no accounts
* refusing to add the same login twice
* re-authentication replacing a stored password
* the options flow changing the monitored accounts

Covered by `tests/test_init.py`:

* `async_setup_entry` creating the meter device and all six usage sensors
* sensor values, units and device classes for the mocked readings
* long-term statistics actually being written to the recorder database
* setup retry when National Grid is unreachable, and re-auth when it returns 401
* unload closing the client and marking entities unavailable
* the refresh button re-running the coordinator

### Test environments

Two Home Assistant versions are tested, both on Python 3.13 (required by
`py-nationalgrid` 0.6.8):

| Environment | Requirements file | Home Assistant |
| --- | --- | --- |
| Pinned | `requirements_test.txt` | 2026.2.3 + harness 0.13.316 |
| Latest | `requirements_test_latest.txt` | newest release (unpinned harness) |

The pinned job is reproducible; the latest job exists to catch upstream breaks
in Home Assistant or the harness. GitHub Actions runs ruff (including
`scripts/`), hassfest, HACS validation and both test environments on every push
and pull request, see [`.github/workflows/tests.yml`](.github/workflows/tests.yml).
To run the latest environment locally:

```bash
python3.13 -m venv .venv-latest
.venv-latest/bin/pip install -r requirements_test_latest.txt
.venv-latest/bin/python -m pytest
```
