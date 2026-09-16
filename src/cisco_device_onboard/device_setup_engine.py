#!/usr/bin/env python3
"""Standalone IOS-XE image upgrade and cloud-management bootstrap utility.

Only PyYAML and pexpect are required.  This file can be copied without the
repository: it does not import the repository's framework, pyATS, or testbed
files.  It opens an interactive SSH or Telnet console, uses the IOS-XE image
copy command, and then performs the upgrade and configuration workflow
described by the YAML input.
"""

import argparse
import json
import logging
import re
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import pexpect
except ImportError:  # The API-only command and validation helpers do not need it.
    pexpect = None
try:
    import yaml
except ImportError:
    yaml = None


LOG = logging.getLogger("iosxe-upgrade")
DEFAULT_TIMEOUTS = {
    "copy": 1800,
    "dhcp": 300,
    "install": 600,
    "prompt": 180,
    "reboot": 1800,
    "poll_interval": 15,
}
# Require a hostname and the prompt marker at the end of the received text.
# This avoids matching an echoed command such as ``Router#dir ...`` while also
# handling boot/syslog text that is immediately adjacent to the prompt.
PROMPT_PATTERN = r"(?m)[A-Za-z0-9_.-]+(?:\([^\r\n)]*\))?[>#][ \t]*(?=\r?\n|$)"
PASSWORD_PATTERN = r"(?i)(?:password|passphrase)\s*:"
USERNAME_PATTERN = r"(?i)(?:username|login)\s*:"
RETURN_PATTERN = r"(?i)press\s+(?:return|enter)\s+to\s+get\s+started!?"
BASIC_SETUP_PATTERN = r"(?i)(?:initial configuration dialog|basic configuration dialog|basic management setup).*?(?:\[yes/no\]|\(yes/no\)|:)"
AUTOINSTALL_PATTERN = r"(?i)(?:terminate|abort|stop)\s+autoinstall.*?(?:\[yes\]|\[yes/no\]|\(yes/no\)|:)"
SAVE_CONFIG_PATTERN = r"(?i)(?:save|would you like to save).*configuration.*?(?:\[yes/no\]|\(yes/no\)|:)"
REMOTE_HOST_PATTERN = r"(?i)(?:address|name)\s+of\s+remote\s+host.*?(?:\?|:)"
SOURCE_FILENAME_PATTERN = r"(?i)(?:source\s+filename|source\s+file\s+name).*?(?:\?|:)"
COPY_FAILURE_MARKERS = (
    "%error",
    "%bad",
    "no such file",
    "permission denied",
    "authentication failed",
    "login failed",
    "not logged in",
    "connection refused",
    "connection timed out",
    "invalid input",
    "transfer failed",
)
MAX_CONSOLE_WAKEUPS = 6


class UpgradeConfigError(ValueError):
    """Raised when the YAML input does not contain a usable configuration."""


def _mapping(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise UpgradeConfigError(f"'{name}' must be a mapping")
    return value


def _required(mapping: Dict[str, Any], key: str, name: str) -> Any:
    value = mapping.get(key)
    if value is None or value == "":
        raise UpgradeConfigError(f"'{name}.{key}' is required")
    return value


def load_config(config_path: Path) -> Dict[str, Any]:
    """Load and validate the user-provided YAML configuration."""

    if yaml is None:
        raise UpgradeConfigError(
            "PyYAML is required for console upgrade; install package dependencies first"
        )

    try:
        with config_path.open(encoding="utf-8") as config_file:
            raw_config = config_file.read()
        normalized_config = "\n".join(
            re.sub(r"^[ \t]*\d+[ \t]", "", line, count=1)
            for line in raw_config.splitlines()
            if line.strip() != "~" and not re.fullmatch(r"[ \t]*\d+[ \t]*", line)
        )
        config = yaml.safe_load(normalized_config)
    except OSError as exc:
        raise UpgradeConfigError(f"Unable to read {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise UpgradeConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

    config = _mapping(config, "root")
    device = _mapping(config.get("device"), "device")
    connection = _mapping(device.get("connection"), "device.connection")
    image = _mapping(config.get("image"), "image")
    source = _mapping(image.get("source"), "image.source")
    server = _mapping(source.get("server"), "image.source.server")

    protocol = str(_required(connection, "protocol", "device.connection")).lower()
    if protocol not in {"ssh", "telnet"}:
        raise UpgradeConfigError(
            "'device.connection.protocol' must be 'ssh' or 'telnet'"
        )
    _required(connection, "ip", "device.connection")
    if protocol == "ssh":
        for key in ("username", "password"):
            _required(connection, key, "device.connection")
    for key in ("ip", "username", "password"):
        _required(server, key, "image.source.server")

    transfer_protocol = str(source.get("protocol", "scp")).strip().lower()
    if transfer_protocol not in {"scp", "ftp"}:
        raise UpgradeConfigError("'image.source.protocol' must be 'scp' or 'ftp'")

    source_path = str(_required(source, "path", "image.source")).strip()
    if not source_path.startswith("/") or not Path(source_path).name:
        raise UpgradeConfigError(
            "'image.source.path' must be an absolute path containing a filename"
        )
    if any(char in source_path for char in "\r\n"):
        raise UpgradeConfigError("'image.source.path' contains an invalid character")

    wan_interfaces = config.get("wan_interfaces")
    if not isinstance(wan_interfaces, list) or len(wan_interfaces) != 2:
        raise UpgradeConfigError("'wan_interfaces' must contain exactly two interfaces")
    normalized_wans = []
    for index, item in enumerate(wan_interfaces):
        if isinstance(item, str):
            interface_name = item
        elif isinstance(item, dict):
            interface_name = item.get("name", "")
        else:
            interface_name = ""
        interface_name = str(interface_name).strip()
        if not interface_name or any(char in interface_name for char in "\r\n;"):
            raise UpgradeConfigError(
                f"Invalid WAN interface at index {index}: {item!r}"
            )
        normalized_wans.append(interface_name)
    config["wan_interfaces"] = normalized_wans

    timeout_config = _mapping(config.get("timeouts", {}), "timeouts")
    config["timeouts"] = {**DEFAULT_TIMEOUTS, **timeout_config}
    config["device"] = device
    config["image"] = image
    config["image"]["source"] = source
    config["image"]["source"]["server"] = server
    config["image"]["source"]["path"] = source_path
    config["image"]["source"]["protocol"] = transfer_protocol
    config["protocol"] = protocol
    target_version = str(config.get("target_version", "26.2")).strip()
    if not re.fullmatch(r"\d+(?:\.\d+)+", target_version):
        raise UpgradeConfigError("'target_version' must look like 26.2 or 26.2.1")
    config["target_version"] = target_version
    return config


def _image_name(config: Dict[str, Any]) -> str:
    image_name = Path(config["image"]["source"]["path"]).name
    if not image_name.lower().endswith(".bin"):
        raise UpgradeConfigError(f"Expected an IOS-XE .bin image, got {image_name}")
    return image_name


def _target_markers(image_name: str) -> Set[str]:
    lower_name = image_name.lower()
    markers = {lower_name}
    build_match = re.search(r"(bld_[a-z0-9_]+)", lower_name, re.IGNORECASE)
    if build_match:
        markers.add(build_match.group(1).lower())
    version_match = re.search(r"universalk9[.-](.+?)\.(?:spa|ssa)\.bin$", lower_name)
    if version_match:
        image_version = version_match.group(1)
        markers.add(image_version)
        release_match = re.match(r"(\d+(?:\.\d+){2})", image_version)
        if release_match:
            markers.add(release_match.group(1))
    v_match = re.search(r"_v(\d+(?:_\d+)+)", lower_name)
    if v_match:
        markers.add(v_match.group(1).replace("_", "."))
    return markers


def _normalize_versions(text: str) -> str:
    return re.sub(r"\d+", lambda match: str(int(match.group(0))), text.lower())


def _version_tuple(value: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _running_version(show_version: str) -> Optional[str]:
    match = re.search(r"(?i)\bversion\s+(\d+(?:\.\d+)+)", show_version)
    return match.group(1) if match else None


def _version_at_least(running: Optional[str], target: str) -> bool:
    if not running:
        return False
    left = _version_tuple(running)
    right = _version_tuple(target)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) >= right + (0,) * (width - len(right))


def _target_is_active(show_version: str, image_name: str) -> bool:
    normalized_output = _normalize_versions(show_version)
    for marker in _target_markers(image_name):
        normalized_marker = _normalize_versions(marker)
        if re.fullmatch(r"\d+(?:\.\d+)+", normalized_marker):
            pattern = rf"(?<![\d.]){re.escape(normalized_marker)}(?![\d.])"
            if re.search(pattern, normalized_output):
                return True
        elif normalized_marker in normalized_output:
            return True
    return False


def _copy_failed(output: str) -> bool:
    """Return whether IOS-XE reported a definite image transfer failure."""

    output_lower = output.lower()
    return any(marker in output_lower for marker in COPY_FAILURE_MARKERS)


def _image_is_present(output: str, image_name: str) -> bool:
    """Check directory output without trusting the echoed ``dir`` command."""

    image_name_lower = image_name.lower()
    for line in output.splitlines():
        # The console echoes the command, which would otherwise make a failed
        # lookup appear successful because the echoed command contains the
        # filename being searched for.
        if re.search(r"[>#]\s*dir\b", line, re.IGNORECASE):
            continue
        if image_name_lower in line.lower():
            return True
    return False


def _interface_has_ip(output: str, interface_name: str) -> bool:
    """Return whether an interface has a real address in ``show ip int br``."""

    requested_suffix = re.search(r"(\d+(?:/\d+)+)$", interface_name.lower())
    requested = interface_name.lower()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        observed = fields[0].lower()
        same_interface = observed == requested
        if requested_suffix:
            same_interface = same_interface or observed.endswith(
                requested_suffix.group(1)
            )
        if same_interface:
            return fields[1].lower() not in {"unassigned", "unknown", "-"}
    return False


class ConsoleSession:
    """Interactive IOS-XE console session backed by the system SSH/Telnet client."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.connection = config["device"]["connection"]
        self.child: Optional[pexpect.spawn] = None
        self.passwords: List[str] = []

    def _spawn(self) -> Tuple[str, List[str], List[str]]:
        protocol = self.config["protocol"]
        host = str(self.connection["ip"])
        port = str(self.connection.get("port", 22 if protocol == "ssh" else 23))
        username = str(self.connection.get("username", ""))
        password = str(self.connection.get("password", ""))
        proxy = self.connection.get("proxy")
        jump_host = self.config.get("jump_host")
        if proxy and not jump_host:
            raise UpgradeConfigError(
                "'jump_host' is required when a proxy is configured"
            )

        if protocol == "ssh":
            args = [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-p",
                port,
            ]
            args.extend(shlex.split(str(self.connection.get("ssh_options", ""))))
            passwords = [password]
            if proxy:
                jump = _mapping(jump_host, "jump_host")
                jump_user = str(_required(jump, "username", "jump_host"))
                jump_ip = str(_required(jump, "ip", "jump_host"))
                jump_port = str(jump.get("port", 22))
                args.extend(["-J", f"{jump_user}@{jump_ip}:{jump_port}"])
                jump_password = str(jump.get("password", ""))
                if jump_password:
                    passwords.insert(0, jump_password)
            args.append(f"{username}@{host}")
            return "ssh", args, passwords

        if proxy:
            jump = _mapping(jump_host, "jump_host")
            jump_user = str(_required(jump, "username", "jump_host"))
            jump_ip = str(_required(jump, "ip", "jump_host"))
            jump_port = str(jump.get("port", 22))
            args = [
                "-tt",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-p",
                jump_port,
                f"{jump_user}@{jump_ip}",
                "telnet",
                host,
                port,
            ]
            passwords = []
            jump_password = str(jump.get("password", ""))
            if jump_password:
                passwords.append(jump_password)
            if password:
                passwords.append(password)
            return (
                "ssh",
                args,
                passwords,
            )
        return "telnet", [host, port], [password] if password else []

    def connect(self) -> None:
        if pexpect is None:
            raise RuntimeError(
                "pexpect is required for console upgrade; install package dependencies first"
            )
        command, args, self.passwords = self._spawn()
        LOG.info(
            "Opening %s console to %s:%s",
            command,
            self.connection["ip"],
            self.connection.get("port", 23 if command == "telnet" else 22),
        )
        self.child = pexpect.spawn(command, args, encoding="utf-8", timeout=30)
        # Stream device output to the operator while retaining pexpect's
        # captured buffers for prompt matching and error reporting.  pexpect
        # logs reads only, so passwords sent by the script are not printed.
        self.child.logfile_read = sys.stdout
        password_index = 0
        wakeup_attempts = 0
        post_boot_prompt_deadline = None
        while True:
            expect_timeout = 30
            if post_boot_prompt_deadline is not None:
                expect_timeout = min(
                    expect_timeout,
                    max(1, post_boot_prompt_deadline - time.monotonic()),
                )
            index = self.child.expect(
                [
                    r"(?i)are you sure you want to continue connecting.*",
                    PASSWORD_PATTERN,
                    USERNAME_PATTERN,
                    RETURN_PATTERN,
                    BASIC_SETUP_PATTERN,
                    AUTOINSTALL_PATTERN,
                    SAVE_CONFIG_PATTERN,
                    PROMPT_PATTERN,
                    pexpect.EOF,
                    pexpect.TIMEOUT,
                ],
                timeout=expect_timeout,
            )
            if index == 0:
                self.child.sendline("yes")
            elif index == 1:
                if password_index >= len(self.passwords):
                    raise RuntimeError(
                        "Console requested a password, but no console password was configured"
                    )
                self.child.sendline(self.passwords[password_index])
                password_index += 1
            elif index == 2:
                username = self.connection.get("username")
                if not username:
                    raise RuntimeError(
                        "Console requested a username, but no console username was configured"
                    )
                self.child.sendline(str(username))
            elif index == 3:
                LOG.info(
                    "Device is ready; pressing Enter to display the console prompt"
                )
                self.child.send("\r")
                post_boot_prompt_deadline = time.monotonic() + int(
                    self.config["timeouts"]["prompt"]
                )
            elif index == 4:
                LOG.info("Skipping basic setup dialog")
                self.child.sendline("no")
                post_boot_prompt_deadline = time.monotonic() + int(
                    self.config["timeouts"]["prompt"]
                )
            elif index == 5:
                LOG.info("Terminating autoinstall dialog")
                self.child.sendline("yes")
                post_boot_prompt_deadline = time.monotonic() + int(
                    self.config["timeouts"]["prompt"]
                )
            elif index == 6:
                LOG.info("Declining setup configuration save prompt")
                self.child.sendline("no")
                post_boot_prompt_deadline = time.monotonic() + int(
                    self.config["timeouts"]["prompt"]
                )
            elif index == 7:
                LOG.debug("Console prompt detected")
                self._prepare_terminal()
                return
            elif index == 8:
                raise RuntimeError("Console connection closed during login")
            else:
                observed = (self.child.before or "").lower()
                if (
                    post_boot_prompt_deadline is not None
                    and time.monotonic() < post_boot_prompt_deadline
                ):
                    LOG.info("Console prompt has not appeared; pressing Enter again")
                    self.child.send("\r")
                    continue
                if wakeup_attempts < MAX_CONSOLE_WAKEUPS:
                    wakeup_attempts += 1
                    LOG.debug("Console is quiet; sending wake-up Enter")
                    self.child.send("\r")
                    continue
                raise TimeoutError(
                    "Timed out waiting for the console login prompt after wake-up attempts"
                )

    def _prepare_terminal(self) -> None:
        self._reset_to_exec_prompt()
        self._enter_privileged_mode()
        self.execute("terminal length 0")
        self.execute("terminal width 0")

    def _reset_to_exec_prompt(self) -> None:
        if self.child is None:
            raise RuntimeError("Console is not connected")
        self.child.send("\x1a")
        try:
            self.child.expect(PROMPT_PATTERN, timeout=10)
        except pexpect.TIMEOUT:
            self.child.send("\r")
            self.child.expect(PROMPT_PATTERN, timeout=10)

    def _enter_privileged_mode(self) -> None:
        if self.child is None:
            raise RuntimeError("Console is not connected")
        self.child.sendline("enable")
        index = self.child.expect(
            [PASSWORD_PATTERN, PROMPT_PATTERN, pexpect.EOF, pexpect.TIMEOUT],
            timeout=30,
        )
        if index == 0:
            enable_password = self.connection.get("enable_password")
            if not enable_password:
                raise RuntimeError(
                    "Device requested an enable password, but none was configured"
                )
            self.child.sendline(str(enable_password))
            self.child.expect(PROMPT_PATTERN, timeout=30)
        elif index == 1:
            prompt = str(self.child.after or "").strip()
            if prompt.endswith(">"):
                raise RuntimeError(
                    "Unable to enter privileged EXEC mode; console remains at Router>"
                )
        elif index == 2:
            raise RuntimeError("Console closed while entering privileged EXEC mode")
        else:
            raise TimeoutError("Timed out entering privileged EXEC mode")

    def execute(self, command: str, timeout: int = 120) -> str:
        if self.child is None:
            raise RuntimeError("Console is not connected")
        self.child.sendline(command)
        self.child.expect(PROMPT_PATTERN, timeout=timeout)
        return self.child.before or ""

    def copy_image(self, config: Dict[str, Any], image_name: str) -> str:
        source = config["image"]["source"]
        server = source["server"]
        transfer_protocol = str(source.get("protocol", "scp")).strip().lower()
        if transfer_protocol not in {"scp", "ftp"}:
            raise UpgradeConfigError("'image.source.protocol' must be 'scp' or 'ftp'")
        destination = str(config["image"].get("destination", "bootflash:")).strip()
        if not destination.endswith(":"):
            destination += ":"
        if config["image"].get("skip_if_present"):
            output = self.execute(f"dir {destination}{image_name}")
            if not _copy_failed(output) and _image_is_present(output, image_name):
                LOG.info("Image already exists on the device; skipping copy")
                return output

        # IOS-XE accepts the URL form.  The double slash after
        # the server preserves the leading slash as an absolute server path.
        remote_path = source["path"].lstrip("/")
        command = (
            f"copy {transfer_protocol}://{server['username']}@{server['ip']}//{remote_path} "
            f"{destination}{image_name}"
        )
        LOG.info("Copying %s to %s by %s", source["path"], destination, transfer_protocol.upper())
        self.child.sendline(command)
        password_sent = False
        username_sent = False
        output = ""
        while True:
            index = self.child.expect(
                [
                    r"(?i)are you sure you want to continue connecting.*",
                    PASSWORD_PATTERN,
                    USERNAME_PATTERN,
                    REMOTE_HOST_PATTERN,
                    SOURCE_FILENAME_PATTERN,
                    r"(?i)destination filename.*",
                    r"(?i)\[confirm\].*",
                    PROMPT_PATTERN,
                    pexpect.EOF,
                    pexpect.TIMEOUT,
                ],
                timeout=int(config["timeouts"]["copy"]),
            )
            output += self.child.before or ""
            if index == 0:
                self.child.sendline("yes")
            elif index == 1:
                if password_sent:
                    raise RuntimeError(
                        f"{transfer_protocol.upper()} requested the server password more than once"
                    )
                self.child.sendline(str(server["password"]))
                password_sent = True
            elif index == 2:
                if username_sent:
                    raise RuntimeError(
                        f"{transfer_protocol.upper()} requested the server username more than once"
                    )
                self.child.sendline(str(server["username"]))
                username_sent = True
            elif index == 3:
                self.child.sendline(str(server["ip"]))
            elif index == 4:
                self.child.sendline(str(source["path"]))
            elif index == 5:
                self.child.sendline("")
            elif index == 6:
                self.child.sendline("")
            elif index == 7:
                if _copy_failed(output):
                    raise RuntimeError(f"{transfer_protocol.upper()} copy failed:\n{output}")
                verification = self.execute(f"dir {destination}{image_name}")
                combined_output = f"{output}\n{verification}"
                if _copy_failed(verification) or not _image_is_present(
                    combined_output, image_name
                ):
                    raise RuntimeError(
                        f"Image {image_name} was not found on the device after {transfer_protocol.upper()} copy. "
                        f"{transfer_protocol.upper()} output:\n{output}\nDirectory output:\n{verification}"
                    )
                LOG.info("%s copy completed and image verified: %s", transfer_protocol.upper(), image_name)
                return verification
            elif index == 8:
                raise RuntimeError(f"Console closed during {transfer_protocol.upper()} copy: {output}")
            else:
                raise TimeoutError(f"Timed out during {transfer_protocol.upper()} copy: {output}")

    def install_image(self, config: Dict[str, Any], image_name: str) -> str:
        destination = str(config["image"].get("destination", "bootflash:")).strip()
        if not destination.endswith(":"):
            destination += ":"
        command = f"install add file {destination}{image_name} activate commit"
        install_timeout = int(config["timeouts"]["install"])
        deadline = time.monotonic() + install_timeout
        LOG.info("Starting IOS-XE upgrade with: %s", command)
        self.child.sendline(command)
        output = ""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    "Image installation exceeded the total timeout of "
                    f"{install_timeout} seconds"
                )
            try:
                index = self.child.expect(
                    [
                        r"(?i)\[y/n\].*",
                        r"(?i)\[yes/no\].*",
                        r"(?i)\[confirm\].*",
                        PROMPT_PATTERN,
                        pexpect.EOF,
                    ],
                    timeout=remaining,
                )
            except pexpect.TIMEOUT as exc:
                raise TimeoutError(
                    "Image installation exceeded the total timeout of "
                    f"{install_timeout} seconds"
                ) from exc
            output += self.child.before or ""
            if index == 0:
                self.child.sendline("y")
            elif index == 1:
                self.child.sendline("yes")
            elif index == 2:
                self.child.sendline("")
            elif index == 3:
                return output
            else:
                LOG.info("Install console closed; waiting for the device to return")
                return output

    def configure_wan_dhcp(self, config: Dict[str, Any]) -> Dict[str, str]:
        """Enable DHCP on both WAN interfaces and save before upgrading."""

        commands = []
        for interface_name in config["wan_interfaces"]:
            commands.extend(
                [
                    f"interface {interface_name}",
                    "ip address dhcp",
                    "no shutdown",
                    "exit",
                ]
            )
        self.execute("configure terminal")
        for command in commands:
            self.execute(command)
        self.execute("end")

        dhcp_output = self._wait_for_wan_dhcp(config)
        verification = {"show_ip_interface_brief": dhcp_output}
        for interface_name in config["wan_interfaces"]:
            output = self.execute(f"show running-config interface {interface_name}")
            if "ip address dhcp" not in output.lower():
                raise RuntimeError(f"DHCP was not configured on {interface_name}")
            verification[interface_name] = output
        self.execute("write memory")
        LOG.info("WAN DHCP configuration verified and saved")
        return verification

    def _wait_for_wan_dhcp(self, config: Dict[str, Any]) -> str:
        """Wait for both WAN interfaces to receive DHCP addresses."""

        deadline = time.monotonic() + int(config["timeouts"]["dhcp"])
        last_output = ""
        while time.monotonic() < deadline:
            last_output = self.execute("show ip int br")
            missing = [
                interface_name
                for interface_name in config["wan_interfaces"]
                if not _interface_has_ip(last_output, interface_name)
            ]
            if not missing:
                LOG.info("Both WAN interfaces acquired DHCP addresses")
                return last_output
            LOG.info(
                "Waiting for DHCP address on: %s",
                ", ".join(missing),
            )
            time.sleep(
                min(
                    int(config["timeouts"]["poll_interval"]),
                    max(0, deadline - time.monotonic()),
                )
            )
        raise TimeoutError(
            "WAN interfaces did not acquire DHCP addresses within "
            f"{config['timeouts']['dhcp']} seconds. Last 'show ip int br' output:\n"
            f"{last_output}"
        )

    def configure_cloud_management(self, config: Dict[str, Any]) -> Dict[str, str]:
        """Enable and verify cloud management after the upgrade reboot."""

        self.execute("configure terminal")
        self.execute("service cloud-mgmt connect")
        self.execute("end")

        verification = {}
        cloud_config = self.execute("show running-config | include ^service cloud-mgmt")
        if "service cloud-mgmt connect" not in cloud_config.lower():
            raise RuntimeError(
                "service cloud-mgmt connect was not found in running configuration"
            )
        verification["service_cloud_mgmt"] = cloud_config
        verification["cloud_mgmt_connect"] = self.execute("show cloud-mgmt connect")
        self.execute("write memory")
        LOG.info("Cloud management configuration verified and saved")
        return verification

    def close(self) -> None:
        if self.child is not None:
            self.child.close(force=True)
            self.child = None


def _wait_for_image(
    config: Dict[str, Any], image_name: str, session: ConsoleSession
) -> str:
    deadline = time.monotonic() + int(config["timeouts"]["reboot"])
    last_error = None
    first_attempt = True
    while time.monotonic() < deadline:
        # install add activate commit reloads the router, so the original
        # Telnet session is stale. Reconnect before the first show version
        # rather than waiting for a prompt on that old session.
        session.close()
        if not first_attempt:
            time.sleep(
                min(
                    int(config["timeouts"]["poll_interval"]),
                    max(0, deadline - time.monotonic()),
                )
            )
            if time.monotonic() >= deadline:
                break
        first_attempt = False
        try:
            LOG.info("Connecting to the console to verify the post-upgrade image")
            session.connect()
            remaining = max(1, int(deadline - time.monotonic()))
            output = session.execute("show version", timeout=min(60, remaining))
            if _target_is_active(output, image_name):
                LOG.info("Target image is active: %s", image_name)
                return output
            LOG.info("Device is reachable but the target image is not active yet")
        except Exception as exc:
            last_error = exc
            LOG.info("Post-upgrade console verification attempt failed: %s", exc)
    detail = (
        f"last error: {last_error}" if last_error else "target version was not reported"
    )
    raise TimeoutError(
        f"Image {image_name} did not become active within the reboot timeout ({detail})"
    )


def run_upgrade(config: Dict[str, Any]) -> Dict[str, Any]:
    image_name = _image_name(config)
    session = ConsoleSession(config)
    report: Dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "device": config["device"].get("name", "router"),
        "connection_protocol": config["protocol"],
        "target_image": image_name,
        "target_version": config["target_version"],
        "wan_interfaces": config["wan_interfaces"],
        "upgrade_status": "NOT_STARTED",
        "result": "FAILED",
    }
    try:
        session.connect()
        before_show_version = session.execute("show version")
        report["before_show_version"] = before_show_version
        running_version = _running_version(before_show_version)
        report["running_version"] = running_version
        report["pre_upgrade_wan_dhcp"] = session.configure_wan_dhcp(config)
        if _version_at_least(running_version, config["target_version"]):
            report["upgrade_status"] = "SKIPPED"
            report["upgrade_reason"] = (
                f"Running IOS-XE version {running_version} is already at or above "
                f"target {config['target_version']}"
            )
            report["after_show_version"] = before_show_version
        else:
            report["upgrade_status"] = "PERFORMED"
            session.copy_image(config, image_name)
            report["install_output"] = session.install_image(config, image_name)
            report["after_show_version"] = _wait_for_image(config, image_name, session)
        report["cloud_management"] = session.configure_cloud_management(config)
        report["result"] = "PASSED"
        return report
    except Exception as exc:
        report["error"] = str(exc)
        return report
    finally:
        session.close()


def _redacted_summary(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "device": config["device"].get("name", "router"),
        "connection_protocol": config["protocol"],
        "connection_ip": config["device"]["connection"]["ip"],
        "image": config["image"]["source"]["path"],
        "image_server": config["image"]["source"]["server"]["ip"],
        "wan_interfaces": config["wan_interfaces"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config", type=Path, help="YAML file containing device and image details"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the configuration without connecting",
    )
    parser.add_argument(
        "--report", type=Path, help="Write a JSON result report to this path"
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        config = load_config(args.config)
        if args.dry_run:
            print(json.dumps(_redacted_summary(config), indent=2))
            return 0
        report = run_upgrade(config)
        if args.report:
            args.report.write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8"
            )
        if report.get("result") == "PASSED":
            LOG.info("Device upgrade completed successfully")
            return 0
        LOG.error("Device upgrade failed: %s", report.get("error", "unknown error"))
        return 1
    except Exception as exc:
        LOG.error("Device upgrade failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
