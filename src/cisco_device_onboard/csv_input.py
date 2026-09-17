"""CSV input validation shared by the two dedicated commands."""

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set


SERIAL_PATTERN = re.compile(r"^[A-Za-z0-9]{4}(?:-[A-Za-z0-9]{4}){2}$")
NETWORK_COLUMNS = {"network-name", "appliance-serial-number"}
CONSOLE_COLUMNS = {"appliance-serial-number", "console-ip", "console-port"}
KNOWN_COLUMNS = NETWORK_COLUMNS | {"console-ip", "console-port"}


class CsvInputError(ValueError):
    """Raised for invalid or incomplete device CSV input."""


@dataclass(frozen=True)
class DeviceRow:
    row_number: int
    network_name: str
    serial: str
    console_ip: str
    console_port: Optional[int]
    extra: Dict[str, str]


def load_devices(
    path: Path, *, require_network: bool = False, require_console: bool = False
) -> List[DeviceRow]:
    with path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = {str(name).strip() for name in (reader.fieldnames or []) if name}
        required = set()
        if require_network:
            required |= NETWORK_COLUMNS
        if require_console:
            required |= CONSOLE_COLUMNS
        if not required:
            required = {"appliance-serial-number"}
        missing = sorted(required - fieldnames)
        if missing:
            raise CsvInputError(f"CSV is missing required column(s): {', '.join(missing)}")

        rows: List[DeviceRow] = []
        seen: Set[str] = set()
        for row_number, raw in enumerate(reader, start=2):
            values = {str(key).strip(): (value or "").strip() for key, value in raw.items() if key}
            serial = values.get("appliance-serial-number", "").upper()
            if not SERIAL_PATTERN.fullmatch(serial):
                raise CsvInputError(
                    f"CSV row {row_number}: appliance-serial-number must match AAAA-BBBB-CCCC"
                )
            if serial in seen:
                raise CsvInputError(f"CSV row {row_number}: duplicate appliance serial {serial}")
            seen.add(serial)
            network_name = values.get("network-name", "")
            if require_network and not network_name:
                raise CsvInputError(f"CSV row {row_number}: network-name is required")
            console_ip = values.get("console-ip", "")
            console_port_text = values.get("console-port", "")
            console_port: Optional[int] = None
            if console_port_text:
                try:
                    console_port = int(console_port_text)
                except ValueError as error:
                    raise CsvInputError(f"CSV row {row_number}: console-port must be numeric") from error
                if not 1 <= console_port <= 65535:
                    raise CsvInputError(f"CSV row {row_number}: console-port is outside 1-65535")
            if require_console and not console_ip:
                raise CsvInputError(f"CSV row {row_number}: console-ip is required")
            if require_console and console_port is None:
                raise CsvInputError(f"CSV row {row_number}: console-port is required")
            rows.append(
                DeviceRow(
                    row_number=row_number,
                    network_name=network_name,
                    serial=serial,
                    console_ip=console_ip,
                    console_port=console_port,
                    extra={key: value for key, value in values.items() if key not in KNOWN_COLUMNS},
                )
            )
        if not rows:
            raise CsvInputError("CSV contains no device rows")
        return rows
