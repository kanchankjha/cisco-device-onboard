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
- The image must be compatible with the device and there must be enough device storage.
- `dashboard-config` requires a Meraki API key with permission to claim devices and modify networks.
- The Dashboard organization ID is mandatory; the tool does not use a default organization.
- Protect API keys, passwords, local YAML files, and generated reports.

Windows installation is supported, but native Windows console-upgrade execution requires compatible SSH/Telnet clients and a runtime supported by `pexpect`. Validate console access on the target Windows host first. WSL is an alternative when native console behavior is unavailable. `dashboard-config` uses HTTPS and does not require a console client.

## Quick start

1. Install the package using the instructions below.
2. Create `devices.csv` using the CSV format in this guide.
3. Copy the platform-specific YAML example and update the image-server values:
   - `examples/c81xx_device_upgrade.example.yaml` for C81xx.
   - `examples/c82xx_device_upgrade.example.yaml` for C82xx.
4. Set the required credentials as environment variables.
5. Run `console-upgrade --dry-run` to validate the inputs.
6. Run the upgrade with a batch size supported by the host and image server.

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

Replace the image-server values, adjust timeouts if needed, and verify that
the WAN interfaces match the appliance.

| YAML field | Purpose |
|---|---|
| `target_version` | Minimum IOS-XE version; the tool does not downgrade. |
| `image.destination` | Device storage destination, normally `bootflash:`. |
| `image.skip_if_present` | Skip image transfer when the image is already present. |
| `image.source.protocol` | Image-transfer protocol: `scp` or `ftp`. |
| `image.source.path` | Absolute path to the image on the image server. |
| `image.source.server` | Image-server IP/hostname and transfer credentials. |
| `wan_interfaces` | One or more device interfaces used for DHCP and connectivity. |
| `timeouts` | Copy, install, reboot, DHCP, prompt, and image-server ping limits. |

For a Telnet console reachable only through an SSH jump host, uncomment the
proxy block in the YAML example. The tool runs an interactive command equivalent
to `ssh -tt jump-host telnet device-ip device-port`:

```yaml
device:
  connection:
    proxy: true

jump_host:
  ip: 172.29.3.64
  port: 2023
  username: meraki
  password: replace-me
```

The jump-host password can instead be supplied with `JUMP_HOST_PASSWORD`, and
the username with `JUMP_HOST_USERNAME`. Jump-host credentials are separate from
the device console credentials. A proxy-enabled connection requires jump-host
IP, port, username, and password; SSH key-only jump-host authentication is not
currently supported by the automated password-prompt flow.

## Environment variables

| Variable | Purpose |
|---|---|
| `CONSOLE_USERNAME` | Default console username. |
| `CONSOLE_PASSWORD` | Default console password. |
| `ENABLE_PASSWORD` | Enable/secret value used during first boot and privileged EXEC access. |
| `CONSOLE_FALLBACK_PASSWORD` | Password for fallback cloud-managed username `miles`. |
| `JUMP_HOST_USERNAME` | Optional SSH jump-host username override when proxy mode is enabled. |
| `JUMP_HOST_PASSWORD` | Optional SSH jump-host password override when proxy mode is enabled. |
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
| `--report-dir DIR` | Summary CSV and device logs; default `upgrade-reports`. |
| `--dry-run` | Validate and print a redacted plan. |
| `--console-protocol {ssh,telnet}` | Default protocol. |
| `--console-username USER` | Default console username. |
| `--console-password-env NAME` | Console-password variable; default `CONSOLE_PASSWORD`. |
| `--enable-password-env NAME` | Enable-password variable; default `ENABLE_PASSWORD`. |

The workflow completes first-boot setup when required, configures WAN DHCP,
pings the image server from the device, copies and installs the image, verifies
the active image, configures cloud management, and verifies Internet access
through an up/up WAN interface by pinging `8.8.8.8`. If IOS-XE package
verification fails, the tool deletes the copied image and retries the
download/install up to three times before marking the device failed. If no
configured WAN interface is up with an IP address, or the Internet ping fails,
the tool waits up to five minutes, polling every 15 seconds, before marking the
device failed. These intervals can be adjusted with `wan_internet_grace` and
`wan_internet_poll_interval` under `timeouts`. After the install command
returns, the tool monitors the console for `rommon_monitor` seconds for delayed
ROMMON output. If a ROMMON upgrade is detected, it recognizes the intermediate
ROMMON reboot, waits for the device's temporary return on the old IOS-XE image,
and adds `rommon_grace` seconds to the post-upgrade verification deadline for
the subsequent reboot into the target image.

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

The JSON Dashboard report is written to the path supplied with `--report`.

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
├── console-upgrade-summary.csv
├── Q4QA-GQ5H-2U74.log
└── Q4ML-PTXT-SYH2.log
```

Each device log contains console output, ping attempts, steps, retries, errors,
and final status. Known credentials are redacted. The summary CSV contains the
appliance serial and a `PASSED` or `FAILED` status in CSV input order. Detailed
engine results are exchanged through a temporary file and are not retained as
per-device JSON files.

Example summary:

```csv
appliance-serial,status
Q4QA-GQ5H-2U74,PASSED
Q4ML-PTXT-SYH2,FAILED
```

View a live Linux/macOS log:

```bash
tail -f upgrade-report/Q4QA-GQ5H-2U74.log
```

View a live Windows PowerShell log:

```powershell
Get-Content .\upgrade-report\Q4QA-GQ5H-2U74.log -Wait
```
