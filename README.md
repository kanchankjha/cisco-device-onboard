# Cisco Device Onboard User Guide

This package provides two commands:

- `console-upgrade` upgrades Cisco IOS-XE appliances through SSH or Telnet.
- `dashboard-config` claims appliances and assigns them to Meraki Dashboard networks.

## Prerequisites and caveats

- CPython 3.6 or newer. Python 3.6 and 3.7 require legacy dependencies and are end-of-life.
- The host must have a working SSH or Telnet client for `console-upgrade`.
- The device console must be connected to the console server listed in the CSV.
- At least one configured WAN interface must be connected to a DHCP-capable network with Internet access.
- The device must reach the configured SCP/FTP image server. The tool pings the server from the device before copying the image.
- Select `--batch-size` based on the number of simultaneous console sessions
  supported by the host/console server and the image-server/network bandwidth
  available for downloading images to all devices in parallel. Increase it only
  when both resources can support the additional concurrent sessions and image
  transfers.
- The image must be compatible with the device and there must be enough device storage.
- `dashboard-config` requires a Meraki API key with permission to claim devices and modify networks.
- The Dashboard organization ID is mandatory; the tool does not use a default organization.
- Do not commit API keys, passwords, local YAML files, or generated reports.

Windows installation is supported, but native Windows console-upgrade execution requires compatible SSH/Telnet clients and a runtime supported by `pexpect`. Validate console access on the target Windows host first. WSL is an alternative when native console behavior is unavailable. `dashboard-config` uses HTTPS and does not require a console client.

## Installation

### Linux or macOS: online installation with a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

### Linux or macOS: online installation without a virtual environment

```bash
python3 -m pip install --user -e .
export PATH="$HOME/.local/bin:$PATH"
```

### Linux or macOS: offline installation with a virtual environment

The offline bundle must contain wheels compatible with the target Python
version and operating system. No Internet connection is used during install.

```bash
./install_offline.sh
source .venv/bin/activate
```

### Linux or macOS: offline installation without a virtual environment

For Ubuntu 18/Python 3.6, use `python3.6` and a Python 3.6-compatible
wheelhouse:

```bash
PYTHON_BIN=python3.6

"$PYTHON_BIN" -m pip install --user --no-index --find-links offline/wheelhouse \
  'setuptools>=58,<69' 'wheel>=0.37.1,<0.48'
"$PYTHON_BIN" -m pip install --user --no-index --find-links offline/wheelhouse \
  --no-build-isolation .

export PATH="$HOME/.local/bin:$PATH"
```

### Windows: online installation with a virtual environment

Run in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

### Windows: offline installation with a virtual environment

Run in PowerShell from the extracted package directory:

```powershell
.\install_offline.ps1
.\.venv\Scripts\Activate.ps1
```

### Windows: installation without a virtual environment

Run in PowerShell:

```powershell
py -3 -m pip install --user -e .
$UserScripts = Join-Path (& py -3 -m site --user-base) "Scripts"
$env:Path += ";$UserScripts"
```

For offline installation, add `--no-index --find-links offline\wheelhouse` to
both pip commands and install compatible build tools first:

```powershell
py -3 -m pip install --user --no-index --find-links offline\wheelhouse `
  "setuptools>=58,<69" "wheel>=0.37.1,<0.48"
py -3 -m pip install --user --no-index --find-links offline\wheelhouse `
  --no-build-isolation .
```

The offline wheelhouse must match the Windows architecture and Python version.
Build one on an Internet-connected host, for example:

```bash
TARGET_PLATFORMS=win_amd64 PYTHON_VERSIONS=311 ./build_offline_bundle.sh
```

For Ubuntu 18/Python 3.6:

```bash
TARGET_PLATFORMS=manylinux2014_x86_64 PYTHON_VERSIONS=36 ./build_offline_bundle.sh
```

## Input files

Create one CSV row for each appliance:

```csv
network-name,appliance-serial-number,console-ip,console-port
Branch-1,Q4QA-GQ5H-2U74,192.0.2.10,8235
```

| Field | Meaning |
|---|---|
| `network-name` | Dashboard network to create or reuse. |
| `appliance-serial-number` | Cisco appliance serial number. |
| `console-ip` | Console-server or terminal-server address. |
| `console-port` | Console-server TCP port. |

Each serial and console endpoint must be unique in a console-upgrade CSV.
The CSV does not require console username or protocol columns. The protocol is
selected with `--console-protocol` and defaults to Telnet; the username is read
from `--console-username` or `CONSOLE_USERNAME`. Existing optional per-device
columns are tolerated for compatibility, but they are not needed in a new CSV.

Create the YAML file from the platform-specific example:

- `examples/c81xx_device_upgrade.example.yaml` for C81xx appliances.
- `examples/c82xx_device_upgrade.example.yaml` for C82xx appliances.

The generic `examples/device_upgrade.example.yaml` remains available as a
reference. Replace the image-server values and select the WAN interfaces that
match the appliance.

```yaml
target_version: "26.2"

image:
  destination: "bootflash:"
  skip_if_present: false
  source:
    protocol: scp
    path: /path/to/approved-iosxe-image.bin
    server:
      ip: 192.0.2.20
      username: scp-user
      password: replace-me

wan_interfaces:
  - Te0/0/8
  - Te0/0/9

timeouts:
  copy: 1800
  install: 600
  reboot: 1800
  poll_interval: 15
  prompt: 180
  wan_dhcp_grace: 60
  server_ping_attempts: 3
  server_ping_interval: 5
  server_ping_timeout: 2
```

`target_version` is the minimum IOS-XE version; the tool does not downgrade.
`image.source` identifies the image-transfer protocol, absolute image path,
and server credentials. `wan_interfaces` accepts one or more interfaces.
`server_ping_*` controls the device-side reachability check before image copy.

## Environment variables

| Variable | Purpose |
|---|---|
| `CONSOLE_USERNAME` | Default console username. |
| `CONSOLE_PASSWORD` | Default console password. |
| `ENABLE_PASSWORD` | Enable/secret value used during first boot and privileged EXEC access. |
| `CONSOLE_FALLBACK_PASSWORD` | Password for fallback cloud-managed username `miles`. |
| `MERAKI_API_KEY` | Meraki Dashboard API key. |
| `MERAKI_API_BASE_URL` | Optional Dashboard API base URL override. |

Example:

```bash
export CONSOLE_USERNAME=admin
export CONSOLE_PASSWORD='console-password'
export ENABLE_PASSWORD='known-first-boot-secret'
export CONSOLE_FALLBACK_PASSWORD='known-cloud-managed-password'
export MERAKI_API_KEY='your-meraki-api-key'
```

Image-server credentials are currently read from YAML; protect that file.

If the device rejects the console username/password or enable password, the
upgrade stops without treating the failure as a console disconnect. The JSON
report records `failure_category` and `recommended_action`, and the terminal
prints instructions to verify the credentials and restart `console-upgrade`.
Passwords are redacted from reports and logs.

## Running console-upgrade

Dry run validates the CSV/YAML and prints a redacted plan without connecting:

```bash
console-upgrade \
  --csv devices.csv \
  --config device_upgrade.yaml \
  --batch-size 4 \
  --dry-run
```

Execute an upgrade:

```bash
console-upgrade \
  --csv devices.csv \
  --config device_upgrade.yaml \
  --batch-size 4 \
  --report-dir upgrade-report
```

`--batch-size` defaults to `1`. With 20 devices and `--batch-size 4`, five
batches run. Devices in one batch run concurrently; the next batch starts only
after the current batch finishes. A failed device does not stop later devices.
Choose the value according to the host's simultaneous Telnet/SSH session limit,
console-server capacity, and the SCP/FTP image-server bandwidth available for
parallel image downloads. A larger value is not automatically faster and can
overload either the console path or the image server.

Available options:

| Option | Purpose |
|---|---|
| `--csv FILE` | Required device CSV. |
| `--config FILE` | Required image/device YAML. |
| `--batch-size N` | Concurrent devices per batch; default `1`. |
| `--report-dir DIR` | JSON reports and device logs; default `upgrade-reports`. |
| `--dry-run` | Validate and print a redacted plan. |
| `--console-protocol {ssh,telnet}` | Default protocol. |
| `--console-username USER` | Default console username. |
| `--console-password-env NAME` | Console-password variable; default `CONSOLE_PASSWORD`. |
| `--enable-password-env NAME` | Enable-password variable; default `ENABLE_PASSWORD`. |

The workflow completes first-boot setup when required, configures WAN DHCP,
pings the image server from the device, copies and installs the image, verifies
the active image, and configures cloud management.

## Running dashboard-config

Dry run:

```bash
dashboard-config \
  --csv devices.csv \
  --org-id 1234567890123456789 \
  --dry-run
```

Apply changes:

```bash
dashboard-config \
  --csv devices.csv \
  --org-id 1234567890123456789 \
  --apply \
  --report onboarding-result.json
```

For each CSV row, the command creates or reuses an IOS XE network, claims the
serial into organization inventory, adds it as a managed device, and verifies
the assignment. `--org-id` is mandatory.

| Option | Purpose |
|---|---|
| `--csv FILE` | Required device CSV. |
| `--org-id ID` | Required Dashboard organization ID. |
| `--apply` | Required to perform writes. |
| `--dry-run` | Validate and print the intended operation. |
| `--report FILE` | JSON report; default `onboarding-result.json`. |
| `--base-url URL` | API base URL override. |
| `--api-key-env NAME` | API-key variable; default `MERAKI_API_KEY`. |
| `--timeout SECONDS` | API timeout; default `30`. |

## Logs and reports

Console upgrades run in the foreground. The terminal shows batch status,
device status, and step transitions, while complete output is written per
device:

```text
upgrade-report/
├── Q4QA-GQ5H-2U74.json
├── Q4ML-PTXT-SYH2.json
└── logs/
    ├── Q4QA-GQ5H-2U74.log
    └── Q4ML-PTXT-SYH2.log
```

Each device log contains console output, ping attempts, steps, retries, errors,
and final status. Known credentials are redacted. Each JSON report includes
batch metadata, the result, any error, and the corresponding log path.

View a live Linux/macOS log:

```bash
tail -f upgrade-report/logs/Q4QA-GQ5H-2U74.log
```

View a live Windows PowerShell log:

```powershell
Get-Content .\upgrade-report\logs\Q4QA-GQ5H-2U74.log -Wait
```
