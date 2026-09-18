"""Image/server YAML loading for the dedicated console upgrade command."""

import re
import os
from pathlib import Path
from typing import Any, Dict

import yaml


class UpgradeConfigError(ValueError):
    """Raised when the image YAML is incomplete."""


def _normalize_yaml(text: str) -> str:
    lines = []
    for line in text.splitlines():
        normalized = re.sub(r"^[ \t]*\d+[ \t]", "", line, count=1)
        if normalized.strip() == "~" or re.fullmatch(r"[ \t]*\d+[ \t]*", line):
            continue
        lines.append(normalized)
    return "\n".join(lines)


def load_upgrade_config(path: Path) -> Dict[str, Any]:
    try:
        config = yaml.safe_load(_normalize_yaml(path.read_text(encoding="utf-8"))) or {}
    except (OSError, yaml.YAMLError) as error:
        raise UpgradeConfigError(f"Unable to read upgrade YAML: {error}") from error
    if not isinstance(config, dict):
        raise UpgradeConfigError("Upgrade YAML root must be a mapping")
    device = config.get("device") or {}
    if not isinstance(device, dict):
        raise UpgradeConfigError("device must be a mapping when specified")
    connection = device.get("connection") or {}
    if not isinstance(connection, dict):
        raise UpgradeConfigError("device.connection must be a mapping when specified")
    if connection.get("proxy"):
        jump_host = config.get("jump_host") or {}
        if not isinstance(jump_host, dict):
            raise UpgradeConfigError("jump_host must be a mapping when proxy is enabled")
        jump_username = jump_host.get("username") or os.environ.get(
            "JUMP_HOST_USERNAME", ""
        )
        jump_password = jump_host.get("password") or os.environ.get(
            "JUMP_HOST_PASSWORD", ""
        )
        missing_jump_fields = [
            key
            for key, value in (
                ("ip", jump_host.get("ip")),
                ("username", jump_username),
                ("password", jump_password),
            )
            if value is None or str(value).strip() == ""
        ]
        if missing_jump_fields:
            raise UpgradeConfigError(
                "Proxy-enabled console access requires jump_host: "
                + ", ".join(missing_jump_fields)
            )
    target_version = str(config.get("target_version", "26.2")).strip()
    image = config.get("image") or {}
    source = image.get("source") or {}
    server = source.get("server") or {}
    transfer_protocol = str(source.get("protocol", "scp")).strip().lower()
    if transfer_protocol not in {"scp", "ftp"}:
        raise UpgradeConfigError("image.source.protocol must be 'scp' or 'ftp'")
    required = {
        "target_version": target_version,
        "image.path": source.get("path"),
        "image.source.server.ip": server.get("ip"),
        "image.source.server.username": server.get("username"),
        "image.source.server.password": server.get("password"),
    }
    missing = [key for key, value in required.items() if value is None or str(value).strip() == ""]
    if missing:
        raise UpgradeConfigError("Upgrade YAML is missing: " + ", ".join(missing))
    wans = config.get("wan_interfaces")
    if not isinstance(wans, list) or not wans or any(not str(item).strip() for item in wans):
        raise UpgradeConfigError("wan_interfaces must contain at least one interface")
    config["target_version"] = target_version
    config["wan_interfaces"] = [str(item).strip() for item in wans]
    config.setdefault("image", {}).setdefault("destination", "bootflash:")
    config["image"].setdefault("source", source)
    config["image"]["source"]["protocol"] = transfer_protocol
    return config
