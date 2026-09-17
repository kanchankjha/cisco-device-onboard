# Cisco Device Onboard

Tools for upgrading Cisco IOS-XE appliances and onboarding them to Meraki
Dashboard:

- `console-upgrade` upgrades an appliance through an SSH/Telnet console.
- `dashboard-config` claims appliances and verifies their Dashboard assignment.

## Requirements and installation

CPython 3.6 or newer is supported. The console workflow also requires the
system SSH/Telnet client.

Standard installation:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

### Offline installation

The offline bundle must already contain wheels compatible with the target
Python version and operating system. It does not download packages from the
internet.

With a virtual environment:

```bash
./install_offline.sh
source .venv/bin/activate
```

Without a virtual environment on Linux or macOS:

```bash
PYTHON_BIN=python3.6

"$PYTHON_BIN" -m pip install --user --no-index --find-links offline/wheelhouse \
  'setuptools>=58,<69' 'wheel>=0.37.1,<0.48'
"$PYTHON_BIN" -m pip install --user --no-index --find-links offline/wheelhouse \
  --no-build-isolation .

export PATH="$HOME/.local/bin:$PATH"
```

For Ubuntu 18/Python 3.6, create a compatible bundle on an internet-connected
build host before transferring it:

```bash
TARGET_PLATFORMS=manylinux2014_x86_64 PYTHON_VERSIONS=36 ./build_offline_bundle.sh
```

The default bundle build targets Linux x86_64 and Python 3.6 through 3.14.

## Input files

CSV example:

```csv
network-name,appliance-serial-number,console-ip,console-port
Example home,AAAA-BBBB-CCCC,192.0.2.10,8235
```

`console-upgrade` requires `appliance-serial-number`, `console-ip`, and
`console-port`. `dashboard-config` requires `network-name` and
`appliance-serial-number`.

Copy `examples/device_upgrade.example.yaml` to a local file and replace the
image-server values. SCP is the default image-transfer protocol; FTP is also
supported.

## Console upgrade

Preview the operation without connecting to devices:

```bash
console-upgrade \
  --csv devices.csv \
  --config device_upgrade.yaml \
  --dry-run
```

Run the upgrade:

```bash
export CONSOLE_USERNAME=admin
export CONSOLE_PASSWORD='console-password'
export ENABLE_PASSWORD='C1scoOnboard'

console-upgrade \
  --csv devices.csv \
  --config device_upgrade.yaml \
  --report-dir upgrade-reports
```

On first boot, the tool completes the setup dialog, uses option `0` to leave
setup without saving its generated configuration, enables privileged mode, and
suppresses console logging while configuring the device.

`ENABLE_PASSWORD` is optional during first boot. If omitted or invalid, the
tool uses its predefined compliant bootstrap value. For an already
cloud-managed device, the supplied console credentials are tried first. If
they are rejected, the tool makes one fallback attempt with username `miles`
and the password supplied through:

```bash
export CONSOLE_FALLBACK_PASSWORD='known-fallback-password'
```

The fallback password is read from the environment and is not stored in the
repository. SSH reconnects with the fallback username; Telnet tries it on the
next login challenge. If Dashboard-enforced credentials are active, console
access may be unavailable.

The console transcript is streamed to the terminal and retained in the JSON
report. Temporary console disconnects are retried up to three times with a
five-second interval.

One or more WAN interfaces may be listed in `wan_interfaces`. With multiple
interfaces, DHCP is monitored for up to `wan_dhcp_grace` seconds, which is 60
seconds by default. The upgrade continues when at least one interface gets an
IP address and reports unassigned interfaces. It fails only when none get an
IP address.

The tool does not downgrade a device. If the target version is already active,
image copy and installation are skipped, while WAN and cloud-management
verification still run.

## Dashboard configuration

Preview changes:

```bash
dashboard-config \
  --csv devices.csv \
  --dry-run
```

Apply changes:

```bash
export MERAKI_API_KEY='your-key'
dashboard-config \
  --csv devices.csv \
  --apply \
  --report onboarding-result.json
```

Use `MERAKI_ORG_ID`/`--org-id` and `MERAKI_API_BASE_URL`/`--base-url` to
override the organization or API base URL.

## Safety

- Run `--dry-run` before production operations.
- `--apply` is required for Dashboard changes.
- Test with one appliance before using a larger CSV.
- Never commit credentials, local YAML files, or generated reports.
