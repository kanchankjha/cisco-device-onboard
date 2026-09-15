# Cisco Device Onboard

An independent, batch-oriented package for two onboarding prerequisites:

1. Upgrade Cisco IOS-XE appliances through SSH/Telnet console when the running version is below the configured target (26.2 by default), enable WAN DHCP, enable cloud management, and save the configuration.
2. Claim appliances into a Meraki organization, create or reuse a named appliance network, claim the appliance into it, and verify the assignment through the Meraki Dashboard API.

This project is intentionally separate from the Binary DA portal. It has no portal imports and does not perform VLAN, port, MR, AutoVPN, CLI Profile, or customer-facing DA configuration.

## Install

From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

The package installation handles `pexpect`, `PyYAML`, and `requests`. The console utility invokes the existing system SSH/Telnet client; no device-specific Python SDK is required.

## Input files

The CSV can contain additional columns; they are retained by the parser and ignored unless documented by a command. The common format is:

```csv
network-name,appliance-serial-number,console-ip,console-port
Example home,AAAA-BBBB-CCCC,192.0.2.10,8235
```

The console command requires `appliance-serial-number`, `console-ip`, and `console-port`. The API onboarding command requires `network-name` and `appliance-serial-number`; console columns may be blank or omitted.

Copy the example YAML to a local file and replace the image/server values. Do not commit real credentials. The YAML contains the approved image path and SCP server details; console and Meraki credentials are supplied through environment variables.

## Console upgrade

Validate the CSV/YAML and print a redacted plan without connecting:

```bash
binary-da-console-upgrade \
  --csv examples/devices.example.csv \
  --config examples/device_upgrade.example.yaml \
  --dry-run
```

Run the batch:

```bash
export CONSOLE_USERNAME=admin
export CONSOLE_PASSWORD='console-password'
export ENABLE_PASSWORD='enable-password'

binary-da-console-upgrade \
  --csv devices.csv \
  --config device_upgrade.yaml \
  --report-dir upgrade-reports
```

The engine never downgrades a device. It reads `show version`; when the running version is already at or above `target_version`, the image copy/install/reboot portion is marked `SKIPPED`, while WAN DHCP and cloud-management configuration are still verified and saved. Each device gets a JSON report. Reports and temporary per-device YAML files are created with restrictive permissions and secrets are redacted from wrapper output.

Optional per-row CSV columns `console-protocol` and `console-username` override the command defaults. The protocol default is Telnet.

## Meraki API onboarding

Dry-run is read-only and does not require an API key:

```bash
binary-da-meraki-onboard \
  --csv devices.csv \
  --dry-run
```

Apply the inventory claim, network creation/reuse, network claim, and read-back verification:

```bash
export MERAKI_API_KEY='your-key'
binary-da-meraki-onboard \
  --csv devices.csv \
  --apply \
  --report onboarding-result.json
```

The organization defaults to the Binary DA organization and can be overridden with `MERAKI_ORG_ID` or `--org-id`. The API base URL can be overridden with `MERAKI_API_BASE_URL` or `--base-url`. The key is only read from the selected environment variable and is never printed or written to the result report.

If an appliance is already assigned to a network, the row fails with the inventory network ID so it can be cleaned up before retrying. Existing exact-name networks are reused; otherwise an appliance-only network is created.

## Safety and repository hygiene

- `--dry-run` is the only planning mode; `--apply` is required for Meraki writes.
- Real YAML, reports, virtual environments, and local secrets are ignored by Git.
- Use a short-lived, least-privilege Meraki API key where possible and revoke it after the batch.
- Test with one appliance before running a larger CSV.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```
