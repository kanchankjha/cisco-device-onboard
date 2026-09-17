# Cisco Device Onboard

Batch tools for Cisco appliance onboarding:

- `console-upgrade`: upgrade IOS-XE appliances through SSH/Telnet console, enable WAN DHCP, enable cloud management, and save the configuration.
- `dashboard-config`: claim appliances into Meraki Dashboard, create or reuse appliance networks, and verify assignment.

## Install

From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Dependencies are installed automatically. The console workflow uses the host SSH/Telnet client.
The supported interpreter range is CPython 3.6 and newer.

## Offline install

The tarball includes `offline/wheelhouse` and setup scripts for offline installs.
The committed wheelhouse targets Linux x86_64 and includes CPython 3.6 through
3.14; generate a platform-specific bundle when installing elsewhere.

On Linux or macOS:

```bash
tar -xzf cisco_device_onboard-0.1.0.tar.gz
cd cisco_device_onboard-0.1.0
./install_offline.sh
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
tar -xzf cisco_device_onboard-0.1.0.tar.gz
cd cisco_device_onboard-0.1.0
.\install_offline.ps1
.\.venv\Scripts\Activate.ps1
```

### Offline install without a virtual environment

The following Linux/macOS commands install into the current user's Python site
packages and place the command-line scripts under `~/.local/bin`. They do not
contact PyPI or create a virtual environment. Python and `pip` must already be
installed on the offline system.

Use `python3` for the active Python version, or `python3.6` on Ubuntu 18:

```bash
PYTHON_BIN=python3.6

"$PYTHON_BIN" -m pip install --user --no-index --find-links offline/wheelhouse \
  'setuptools>=58,<69' 'wheel>=0.37.1,<0.48'
"$PYTHON_BIN" -m pip install --user --no-index --find-links offline/wheelhouse \
  --no-build-isolation .

export PATH="$HOME/.local/bin:$PATH"
console-upgrade --help
dashboard-config --help
```

The wheelhouse must contain wheels compatible with the selected Python version
and operating system. For Python 3.6 on Ubuntu 18, build or obtain a matching
bundle before transferring the tarball:

```bash
TARGET_PLATFORMS=manylinux2014_x86_64 PYTHON_VERSIONS=36 ./build_offline_bundle.sh
```

The default bundle build includes CPython 3.6 through 3.14. Use
`PYTHON_VERSIONS` to build a smaller bundle for a specific interpreter.

Verify the commands:

```bash
console-upgrade --help
dashboard-config --help
```

Rebuild the offline tarball on a machine with internet access:

```bash
./build_offline_bundle.sh
```

The wheel builder can create bundles for Linux x86_64, Linux ARM64, macOS Intel, macOS Apple Silicon, and Windows x64 across CPython 3.6 and newer. The default build targets Linux x86_64 with CPython 3.6 through 3.14:

```bash
./build_offline_bundle.sh
```

To build only an Ubuntu 18 x86_64 / CPython 3.6 bundle:

```bash
TARGET_PLATFORMS=manylinux2014_x86_64 PYTHON_VERSIONS=36 ./build_offline_bundle.sh
```

Python 3.6 and 3.7 use older dependency versions for compatibility. Those interpreters are end-of-life, so use this legacy bundle only where upgrading the host is not possible.

Override `TARGET_PLATFORMS` to build a smaller or different wheelhouse. On Windows, validate local SSH/Telnet behavior before running `console-upgrade` in production.

## Input files

CSV format:

```csv
network-name,appliance-serial-number,console-ip,console-port
Example home,AAAA-BBBB-CCCC,192.0.2.10,8235
```

`console-upgrade` requires `appliance-serial-number`, `console-ip`, and `console-port`. `dashboard-config` requires `network-name` and `appliance-serial-number`.

Copy `examples/device_upgrade.example.yaml` to a local YAML file and replace the image server values. Set `image.source.protocol` to `scp` or `ftp`; SCP is the default and recommended option. Supply console and Meraki credentials through environment variables.

## Console upgrade

Validate the CSV/YAML and print a redacted plan without connecting:

```bash
console-upgrade \
  --csv examples/devices.example.csv \
  --config examples/device_upgrade.example.yaml \
  --dry-run
```

Run the batch:

```bash
export CONSOLE_USERNAME=admin
export CONSOLE_PASSWORD='console-password'
export ENABLE_PASSWORD='C1scoOnboard'

console-upgrade \
  --csv devices.csv \
  --config device_upgrade.yaml \
  --report-dir upgrade-reports
```

On a first-boot IOS-XE console, the engine answers the initial setup dialog,
supplies the enable secret and console/login password, selects option `0` to
leave setup without saving its generated setup configuration, and then waits
for the IOS prompt. `ENABLE_PASSWORD` is optional for this first-boot path. If
it is omitted, a temporary 12-character value containing uppercase, lowercase,
and a digit is generated and reused for the session. For repeatable access
after the run, set `ENABLE_PASSWORD` to a 12-character value meeting the same
policy. The generated value is never printed or written to the report.

After entering privileged EXEC mode, the engine runs `no logging console` and
disables terminal paging so that device logging does not obscure configuration
and upgrade prompts. The console transcript is streamed to the terminal while
each device is running; the JSON report remains the machine-readable result.
If the console disconnects during an upgrade step, the same session state is
reconnected and the workflow is retried up to three times, waiting five seconds
between attempts. The retry count is recorded in `console_retry_count`.

Run one platform type per batch. Devices with different platform families, console behaviors, image trains, or WAN interface naming should use separate CSV/YAML batches so each run has a single validated upgrade path.

The engine never downgrades a device. If the running version is already at or above `target_version`, image copy/install/reboot is marked `SKIPPED`; WAN DHCP and cloud management are still verified and saved. The appliance must reach the configured SCP/FTP server. Optional CSV columns `console-protocol` and `console-username` override defaults.

## Dashboard configuration

Dry-run:

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

Override organization and API base URL with `MERAKI_ORG_ID` / `--org-id` and `MERAKI_API_BASE_URL` / `--base-url`. Existing exact-name networks are reused.

## Safety and repository hygiene

- Use `--dry-run` before production runs.
- `--apply` is required for Meraki writes.
- Run one platform type per batch.
- Do not commit real credentials, local YAML, reports, or secrets.
- Test with one appliance before running a larger CSV.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```
