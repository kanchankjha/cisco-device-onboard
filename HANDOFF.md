# New-thread handoff

Continue development in `/Users/kkumarjh/git/cisco-device-onboard`.

This is a standalone Python package and must remain independent of `/Users/kkumarjh/git/binary-da-workflow`. It contains two dedicated commands:

- `binary-da-console-upgrade`: reads serial/console connection details from CSV and image/SCP/WAN settings from YAML. It upgrades only when the running IOS-XE version is below `target_version` (26.2 by default), then enables WAN DHCP and cloud management.
- `binary-da-meraki-onboard`: reads network name and appliance serial from CSV, uses `MERAKI_API_KEY`, and performs inventory claim, appliance network create/reuse, network claim, and verification through the Meraki Dashboard API.

Useful checks:

```bash
cd /Users/kkumarjh/git/cisco-device-onboard
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m cisco_device_onboard.console_upgrade --csv examples/devices.example.csv --config examples/device_upgrade.example.yaml --dry-run
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m cisco_device_onboard.meraki_onboard --csv examples/devices.example.csv --dry-run
```

Do not add real console, SCP, or Meraki credentials to Git. The example YAML and CSV are sanitized. The current local commit is `5f6a119`.
