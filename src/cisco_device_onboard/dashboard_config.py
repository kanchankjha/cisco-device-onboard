"""Batch Meraki Dashboard configuration command driven by the shared device CSV."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from .csv_input import CsvInputError, DeviceRow, load_devices


DEFAULT_BASE_URL = "https://api.meraki.com/api/v1"
IOS_XE_NETWORK_DETAILS = [
    {"productType": "appliance", "name": "operating system", "value": "IOS XE"}
]


class MerakiApiError(RuntimeError):
    pass


class MerakiApi:
    def __init__(self, api_key: str, base_url: str, timeout: int = 30):
        if not api_key:
            raise MerakiApiError("MERAKI_API_KEY is not configured")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"X-Cisco-Meraki-API-Key": api_key, "Accept": "application/json"})

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        response = self.session.request(method, self.base_url + path, json=payload, timeout=self.timeout)
        if not response.ok:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text.strip()
            raise MerakiApiError(f"{method} {path} returned HTTP {response.status_code}: {detail}")
        return response.json() if response.content else {}

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, payload: Dict[str, Any]) -> Any:
        return self.request("POST", path, payload)


def inventory_device(api: MerakiApi, org_id: str, serial: str) -> Optional[Dict[str, Any]]:
    devices = api.get(f"/organizations/{org_id}/inventory/devices")
    if isinstance(devices, dict):
        devices = devices.get("items", devices.get("devices", []))
    return next((item for item in devices or [] if str(item.get("serial", "")).upper() == serial), None)


def network_by_name(api: MerakiApi, org_id: str, name: str) -> Optional[Dict[str, Any]]:
    networks = api.get(f"/organizations/{org_id}/networks")
    return next((item for item in networks or [] if item.get("name") == name), None)


def is_ios_xe_network(network: Dict[str, Any]) -> bool:
    details = network.get("details") or []
    return any(
        isinstance(detail, dict)
        and str(detail.get("productType", "")).lower() == "appliance"
        and str(detail.get("name", "")).lower() == "operating system"
        and str(detail.get("value", "")).upper() == "IOS XE"
        for detail in details
    )


def onboard_row(
    api: MerakiApi,
    org_id: str,
    row: DeviceRow,
    progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    progress = progress or (lambda message: None)

    progress("STEP 1/4: Find or create the IOS XE network")
    network = network_by_name(api, org_id, row.network_name)
    if not network:
        progress(f"Creating network '{row.network_name}' for IOS XE appliance")
        network = api.post(
            f"/organizations/{org_id}/networks",
            {
                "name": row.network_name,
                "productTypes": ["appliance"],
                "details": IOS_XE_NETWORK_DETAILS,
            },
        )
    else:
        progress(f"Using existing network {network.get('id', '<unknown>')}")
        if not is_ios_xe_network(network):
            raise MerakiApiError(
                f"Network '{row.network_name}' ({network.get('id', '<unknown>')}) "
                "is not configured as an IOS XE network. Delete/recreate it as "
                "an IOS XE network or use a different network name."
            )

    network_id = str(network["id"])
    progress("STEP 2/4: Check and claim the appliance in organization inventory")
    inventory = inventory_device(api, org_id, row.serial)
    if inventory and inventory.get("networkId"):
        raise MerakiApiError(
            f"{row.serial} is already assigned to network {inventory['networkId']}; clean it up before onboarding"
        )
    if not inventory:
        progress(f"Claiming serial {row.serial} into organization inventory")
        api.post(f"/organizations/{org_id}/inventory/claim", {"serials": [row.serial]})
    else:
        progress(f"Serial {row.serial} is already available in organization inventory")

    progress(f"STEP 3/4: Add serial {row.serial} to network {network_id}")
    devices = api.get(f"/networks/{network_id}/devices")
    if not any(str(item.get("serial", "")).upper() == row.serial for item in devices or []):
        api.post(
            f"/networks/{network_id}/devices/claim?addAtomically=true",
            {"serials": [row.serial], "detailsByDevice": [{"serial": row.serial, "details": [{"name": "device mode", "value": "managed"}]}]},
        )
    else:
        progress(f"Serial {row.serial} is already present in network {network_id}")

    progress(f"STEP 4/4: Verify serial {row.serial} in network {network_id}")
    verified = api.get(f"/networks/{network_id}/devices")
    if not any(str(item.get("serial", "")).upper() == row.serial for item in verified or []):
        raise MerakiApiError(f"{row.serial} was not visible in network {network_id} after claim")
    progress(f"COMPLETED: serial {row.serial} is assigned to network {network_id}")
    return {"row": row.row_number, "serial": row.serial, "networkName": row.network_name, "networkId": network_id}


def _progress(row: DeviceRow, message: str) -> None:
    print(
        f"[row={row.row_number} serial={row.serial} network={row.network_name}] {message}",
        flush=True,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--org-id", required=True, help="Meraki Dashboard organization ID")
    parser.add_argument("--base-url", default=os.environ.get("MERAKI_API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key-env", default="MERAKI_API_KEY")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Allow claims and network/device writes")
    parser.add_argument("--report", type=Path, default=Path("onboarding-result.json"))
    args = parser.parse_args(argv)
    try:
        rows = load_devices(args.csv, require_network=True)
        api_key = os.environ.get(args.api_key_env, "")
        if args.dry_run:
            print(json.dumps({"orgId": args.org_id, "rows": [row.__dict__ for row in rows]}, indent=2))
            return 0
        if not args.apply:
            raise MerakiApiError("Writes require --apply; use --dry-run to inspect the CSV")
        api = MerakiApi(api_key, args.base_url, args.timeout)
        results = []
        for row in rows:
            _progress(row, "START dashboard onboarding")
            try:
                results.append(
                    {
                        "result": "success",
                        **onboard_row(
                            api,
                            args.org_id,
                            row,
                            progress=lambda message, current=row: _progress(current, message),
                        ),
                    }
                )
            except MerakiApiError as error:
                _progress(row, f"FAILED: {error}")
                results.append({"result": "failed", "row": row.row_number, "serial": row.serial, "error": str(error)})
            finally:
                _progress(row, "END dashboard onboarding")
        args.report.write_text(json.dumps({"results": results}, indent=2), encoding="utf-8")
        print(json.dumps({"results": results}, indent=2))
        return 0 if all(item["result"] == "success" for item in results) else 1
    except (CsvInputError, MerakiApiError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
