"""Batch console upgrade command driven by CSV rows and one image YAML file."""

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .csv_input import CsvInputError, DeviceRow, load_devices
from .upgrade_config import UpgradeConfigError, load_upgrade_config


ENGINE_PATH = Path(__file__).with_name("device_setup_engine.py")


def _env_or_empty(name: str) -> str:
    return os.environ.get(name, "").strip()


def _redact(value: Any, secrets: List[str]) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    return value


def _known_secrets(config: Dict[str, Any]) -> List[str]:
    connection = config.get("device", {}).get("connection", {})
    source = config.get("image", {}).get("source", {})
    server = source.get("server", {})
    jump = config.get("jump_host", {})
    return [
        str(connection.get("password", "")),
        str(connection.get("enable_password", "")),
        str(server.get("password", "")),
        str(jump.get("password", "")) if isinstance(jump, dict) else "",
    ]


def build_device_config(base: Dict[str, Any], row: DeviceRow, args: argparse.Namespace) -> Dict[str, Any]:
    if not row.console_ip or row.console_port is None:
        raise CsvInputError(f"CSV row {row.row_number}: console connection details are required")
    config = copy.deepcopy(base)
    connection = config.setdefault("device", {}).setdefault("connection", {})
    protocol = (row.extra.get("console-protocol") or args.console_protocol or "telnet").lower()
    connection.update({"protocol": protocol, "ip": row.console_ip, "port": row.console_port})
    username = row.extra.get("console-username") or args.console_username or _env_or_empty("CONSOLE_USERNAME")
    password = args.console_password or _env_or_empty(args.console_password_env)
    enable_password = args.enable_password or _env_or_empty(args.enable_password_env)
    if username:
        connection["username"] = username
    if password:
        connection["password"] = password
    if enable_password:
        connection["enable_password"] = enable_password
    config["device"]["name"] = row.serial
    return config


def redacted_plan(rows: List[DeviceRow], config: Dict[str, Any]) -> Dict[str, Any]:
    server = config["image"]["source"]["server"]
    return {
        "rows": [
            {
                "row": row.row_number,
                "serial": row.serial,
                "consoleIp": row.console_ip,
                "consolePort": row.console_port,
                "networkName": row.network_name,
            }
            for row in rows
        ],
        "targetVersion": config.get("target_version", "26.2"),
        "image": config["image"]["source"].get("path"),
        "imageServer": server.get("ip"),
        "wanInterfaces": config.get("wan_interfaces"),
        "timeouts": config.get("timeouts", {}),
    }


def upgrade_row(row: DeviceRow, config: Dict[str, Any], report_dir: Path, args: argparse.Namespace) -> Dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"cisco-device-{row.serial}-") as temp_dir:
        temp_path = Path(temp_dir)
        config_path = temp_path / "device_upgrade.yaml"
        report_path = temp_path / "upgrade-result.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        os.chmod(config_path, 0o600)
        command = [sys.executable, str(ENGINE_PATH), str(config_path), "--report", str(report_path)]
        completed = subprocess.run(
            command,
            cwd=ENGINE_PATH.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        result: Dict[str, Any] = {"serial": row.serial, "row": row.row_number}
        secrets = _known_secrets(config)
        if report_path.is_file():
            try:
                result.update(_redact(json.loads(report_path.read_text(encoding="utf-8")), secrets))
            except (OSError, json.JSONDecodeError):
                pass
        if completed.returncode == 0:
            result.setdefault("result", "PASSED")
        else:
            result["result"] = "FAILED"
            error_text = (completed.stderr or completed.stdout or "Console upgrade failed").strip()
            result["error"] = _redact(
                error_text.splitlines()[-1] if error_text else "Console upgrade failed",
                secrets,
            )
        result = _redact(result, secrets)
        output_path = report_dir / f"{row.serial}.json"
        output_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        os.chmod(output_path, 0o600)
        return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True, help="Image/server YAML configuration")
    parser.add_argument("--report-dir", type=Path, default=Path("upgrade-reports"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--console-protocol", choices=("ssh", "telnet"), default="telnet")
    parser.add_argument("--console-username", default="")
    parser.add_argument("--console-password-env", default="CONSOLE_PASSWORD")
    parser.add_argument("--enable-password-env", default="ENABLE_PASSWORD")
    parser.add_argument("--console-password", default="", help=argparse.SUPPRESS)
    parser.add_argument("--enable-password", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        rows = load_devices(args.csv, require_console=True)
        config = load_upgrade_config(args.config)
        plan = redacted_plan(rows, config)
        if args.dry_run:
            print(json.dumps(plan, indent=2))
            return 0
        if not ENGINE_PATH.is_file():
            raise CsvInputError(f"Console engine is missing: {ENGINE_PATH}")
        results = []
        for row in rows:
            results.append(upgrade_row(row, build_device_config(config, row, args), args.report_dir, args))
        print(json.dumps({"results": results}, indent=2, default=str))
        return 0 if all(item.get("result") in {"PASSED", "success", "skipped"} for item in results) else 1
    except (CsvInputError, UpgradeConfigError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
