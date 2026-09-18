"""Batch console upgrade command driven by CSV rows and one image YAML file."""

import argparse
import concurrent.futures
import copy
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .csv_input import CsvInputError, DeviceRow, load_devices
from .upgrade_config import UpgradeConfigError, load_upgrade_config


ENGINE_PATH = Path(__file__).with_name("device_setup_engine.py")
_STATUS_SENTINEL = object()
_OUTPUT_LOCK = threading.Lock()
_ACTIVE_PROCESSES = set()
_ACTIVE_PROCESSES_LOCK = threading.Lock()


def _safe_print(message: str) -> None:
    with _OUTPUT_LOCK:
        print(message, flush=True)


def _register_active_process(process: subprocess.Popen) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.add(process)


def _unregister_active_process(process: subprocess.Popen) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.discard(process)


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass


def _terminate_active_processes() -> None:
    with _ACTIVE_PROCESSES_LOCK:
        processes = list(_ACTIVE_PROCESSES)
    for process in processes:
        _terminate_process(process)


class StatusPrinter:
    """Serialize foreground status output from concurrent device workers."""

    def __init__(self):
        self._events = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="console-upgrade-status",
        )

    def start(self) -> None:
        self._thread.start()

    def emit(self, message: str) -> None:
        self._events.put(message)

    def close(self) -> None:
        self._events.put(_STATUS_SENTINEL)
        self._thread.join()

    def _run(self) -> None:
        while True:
            message = self._events.get()
            if message is _STATUS_SENTINEL:
                return
            _safe_print(message)


def _display_value(value: Any, fallback: str = "-") -> str:
    text = str(value).strip() if value is not None else ""
    return text.replace("\r", " ").replace("\n", " ") or fallback


def _row_prefix(row: DeviceRow) -> str:
    return (
        f"[device={_display_value(row.serial)} "
        f"network={_display_value(row.network_name)}]"
    )


def _status_from_engine_line(text: str) -> Optional[str]:
    step = re.search(r"\b(STEP\s+\d+/\d+:.*)$", text)
    if step:
        return step.group(1).strip()
    if re.search(r"\b(?:ERROR|WARNING)\b", text):
        return text.strip()
    return None


def _device_log_path(report_dir: Path, row: DeviceRow) -> Path:
    return report_dir / "logs" / f"{row.serial}.log"


def _write_result_report(
    report_dir: Path, row: DeviceRow, result: Dict[str, Any]
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    output_path = report_dir / f"{row.serial}.json"
    output_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    os.chmod(output_path, 0o600)
    return output_path


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
        _env_or_empty("CONSOLE_FALLBACK_PASSWORD"),
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


def upgrade_row(
    row: DeviceRow,
    config: Dict[str, Any],
    report_dir: Path,
    args: argparse.Namespace,
    status_emit: Optional[Callable[[str], None]] = None,
    show_console_output: bool = True,
    batch_number: int = 1,
    batch_total: int = 1,
) -> Dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    log_path = _device_log_path(report_dir, row)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = _row_prefix(row)
    status_emit = status_emit or _safe_print
    secrets = _known_secrets(config)
    last_status = None
    console_tail = deque(maxlen=80)

    with log_path.open("w", encoding="utf-8", buffering=1) as log_file:
        def write_log(value: str) -> str:
            redacted = _redact(value, secrets)
            log_file.write(redacted)
            if not redacted.endswith("\n"):
                log_file.write("\n")
            log_file.flush()
            return redacted

        write_log(
            f"START console upgrade (CSV row {row.row_number}, "
            f"batch {batch_number}/{batch_total})\n"
        )
        write_log(
            f"target={_display_value(config.get('target_version'))} "
            f"image={_display_value(config.get('image', {}).get('source', {}).get('path'))}\n"
        )
        status_emit(f"{prefix} START batch={batch_number}/{batch_total}")
        status_emit(
            f"{prefix} target={_display_value(config.get('target_version'))} "
            f"image={_display_value(config.get('image', {}).get('source', {}).get('path'))}"
        )

        with tempfile.TemporaryDirectory(prefix=f"cisco-device-{row.serial}-") as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "device_upgrade.yaml"
            report_path = temp_path / "upgrade-result.json"
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            os.chmod(config_path, 0o600)
            command = [
                sys.executable,
                "-u",
                str(ENGINE_PATH),
                str(config_path),
                "--report",
                str(report_path),
            ]
            popen_kwargs = {
                "cwd": ENGINE_PATH.parent,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "universal_newlines": True,
                "bufsize": 1,
            }
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
            completed = subprocess.Popen(command, **popen_kwargs)
            _register_active_process(completed)
            try:
                if completed.stdout is not None:
                    for line in iter(completed.stdout.readline, ""):
                        redacted_line = write_log(line)
                        console_tail.append(redacted_line)
                        text = redacted_line.rstrip("\r\n")
                        if show_console_output:
                            status_emit(f"{prefix} {text}" if text else prefix)
                        else:
                            current_status = _status_from_engine_line(text)
                            if current_status and current_status != last_status:
                                status_emit(f"{prefix} {current_status}")
                                last_status = current_status
                    completed.stdout.close()
                return_code = completed.wait()
            except BaseException:
                _terminate_process(completed)
                raise
            finally:
                _unregister_active_process(completed)

            result: Dict[str, Any] = {
                "serial": row.serial,
                "row": row.row_number,
                "batch_number": batch_number,
                "batch_total": batch_total,
                "log_file": str(log_path),
            }
            if report_path.is_file():
                try:
                    result.update(
                        _redact(json.loads(report_path.read_text(encoding="utf-8")), secrets)
                    )
                except (OSError, json.JSONDecodeError):
                    pass
            if return_code == 0:
                result.setdefault("result", "PASSED")
            else:
                result["result"] = "FAILED"
                error_text = "".join(console_tail).strip() or "Console upgrade failed"
                result["error"] = _redact(
                    error_text.splitlines()[-1],
                    secrets,
                )
            result.update(
                {
                    "serial": row.serial,
                    "row": row.row_number,
                    "batch_number": batch_number,
                    "batch_total": batch_total,
                    "log_file": str(log_path),
                }
            )
            result = _redact(result, secrets)
            write_log(f"RESULT {result.get('result', 'FAILED')}\n")
            output_path = _write_result_report(report_dir, row, result)
            status_emit(
                f"{prefix} END result={result.get('result', 'FAILED')} "
                f"log={log_path} report={output_path}"
            )
            return result


def _record_row_failure(
    row: DeviceRow,
    report_dir: Path,
    error: Exception,
    batch_number: int,
    batch_total: int,
    status_emit: Callable[[str], None],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    report_dir.mkdir(parents=True, exist_ok=True)
    log_path = _device_log_path(report_dir, row)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    secrets = _known_secrets(config or {})
    mode = "a" if log_path.exists() else "w"
    with log_path.open(mode, encoding="utf-8", buffering=1) as log_file:
        log_file.write(f"ERROR before completion: {_redact(str(error), secrets)}\n")
        log_file.flush()
    result = {
        "serial": row.serial,
        "row": row.row_number,
        "batch_number": batch_number,
        "batch_total": batch_total,
        "result": "FAILED",
        "error": _redact(str(error), secrets),
        "log_file": str(log_path),
    }
    output_path = _write_result_report(report_dir, row, result)
    status_emit(
        f"{_row_prefix(row)} RESULT FAILED log={log_path} report={output_path}"
    )
    return result


def _run_row(
    row: DeviceRow,
    base_config: Dict[str, Any],
    report_dir: Path,
    args: argparse.Namespace,
    status_emit: Callable[[str], None],
    batch_number: int,
    batch_total: int,
) -> Dict[str, Any]:
    config = None
    try:
        config = build_device_config(base_config, row, args)
        return upgrade_row(
            row,
            config,
            report_dir,
            args,
            status_emit=status_emit,
            show_console_output=False,
            batch_number=batch_number,
            batch_total=batch_total,
        )
    except Exception as error:
        return _record_row_failure(
            row,
            report_dir,
            error,
            batch_number,
            batch_total,
            status_emit,
            config=config,
        )


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True, help="Image/server YAML configuration")
    parser.add_argument("--report-dir", type=Path, default=Path("upgrade-reports"))
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=1,
        help="Number of devices to upgrade concurrently in each batch (default: 1)",
    )
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
        plan["batchSize"] = args.batch_size
        if args.dry_run:
            print(json.dumps(plan, indent=2))
            return 0
        if not ENGINE_PATH.is_file():
            raise CsvInputError(f"Console engine is missing: {ENGINE_PATH}")
        results = []
        batch_total = (len(rows) + args.batch_size - 1) // args.batch_size
        status_printer = StatusPrinter()
        status_printer.start()
        try:
            for batch_number, offset in enumerate(
                range(0, len(rows), args.batch_size), start=1
            ):
                batch_rows = rows[offset : offset + args.batch_size]
                serials = ",".join(row.serial for row in batch_rows)
                status_printer.emit(
                    f"BATCH {batch_number}/{batch_total} START devices={serials}"
                )
                executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(batch_rows),
                    thread_name_prefix=f"console-batch-{batch_number}",
                )
                try:
                    futures = [
                        executor.submit(
                            _run_row,
                            row,
                            config,
                            args.report_dir,
                            args,
                            status_printer.emit,
                            batch_number,
                            batch_total,
                        )
                        for row in batch_rows
                    ]
                    for row, future in zip(batch_rows, futures):
                        try:
                            results.append(future.result())
                        except Exception as error:
                            results.append(
                                _record_row_failure(
                                    row,
                                    args.report_dir,
                                    error,
                                    batch_number,
                                    batch_total,
                                    status_printer.emit,
                                    config=config,
                                )
                            )
                except KeyboardInterrupt:
                    _terminate_active_processes()
                    status_printer.emit(
                        f"BATCH {batch_number}/{batch_total} INTERRUPTED; stopping active devices"
                    )
                    raise
                finally:
                    executor.shutdown(wait=True)
                status_printer.emit(f"BATCH {batch_number}/{batch_total} END")
        finally:
            status_printer.close()
        _safe_print(json.dumps({"results": results}, indent=2, default=str))
        return 0 if all(item.get("result") in {"PASSED", "success", "skipped"} for item in results) else 1
    except KeyboardInterrupt:
        _terminate_active_processes()
        _safe_print("ERROR: Console upgrade interrupted")
        return 130
    except (CsvInputError, UpgradeConfigError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
