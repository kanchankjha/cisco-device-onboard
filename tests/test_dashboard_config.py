import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from cisco_device_onboard.dashboard_config import MerakiApiError, main, onboard_row
from cisco_device_onboard.csv_input import DeviceRow


class DashboardConfigTests(unittest.TestCase):
    def row(self):
        return DeviceRow(
            row_number=2,
            network_name="network1",
            serial="Q4QA-GQ5H-2U74",
            console_ip="",
            console_port=None,
            extra={},
        )

    def test_creates_ios_xe_network_then_claims_and_verifies_device(self):
        api = mock.Mock()
        api.get.side_effect = [
            [],
            [],
            [],
            [{"serial": "Q4QA-GQ5H-2U74"}],
        ]
        api.post.side_effect = [
            {"id": "N_1", "details": [{"productType": "appliance", "value": "IOS XE"}]},
            {},
            {},
        ]
        progress = []

        result = onboard_row(api, "123", self.row(), progress.append)

        self.assertEqual(result["networkId"], "N_1")
        self.assertEqual(
            api.post.call_args_list[0],
            mock.call(
                "/organizations/123/networks",
                {
                    "name": "network1",
                    "productTypes": ["appliance"],
                    "details": [
                        {
                            "productType": "appliance",
                            "name": "operating system",
                            "value": "IOS XE",
                        }
                    ],
                },
            ),
        )
        self.assertEqual(
            [call.args[0] for call in api.post.call_args_list],
            [
                "/organizations/123/networks",
                "/organizations/123/inventory/claim",
                "/networks/N_1/devices/claim?addAtomically=true",
            ],
        )
        self.assertEqual(progress[0], "STEP 1/4: Find or create the IOS XE network")
        self.assertIn("STEP 4/4: Verify", progress[-2])
        self.assertTrue(progress[-1].startswith("COMPLETED:"))

    def test_existing_non_ios_xe_network_is_rejected_before_claim(self):
        api = mock.Mock()
        api.get.return_value = [
            {"id": "N_wrong", "name": "network1", "productTypes": ["appliance"]}
        ]

        with self.assertRaisesRegex(MerakiApiError, "not configured as an IOS XE network"):
            onboard_row(api, "123", self.row())

        api.post.assert_not_called()

    def test_org_id_is_required(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        handle.write("network-name,appliance-serial-number\nnetwork1,Q4QA-GQ5H-2U74\n")
        handle.close()
        self.addCleanup(Path(handle.name).unlink)

        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with self.assertRaises(SystemExit) as error:
                main(["--csv", handle.name, "--dry-run"])

        self.assertEqual(error.exception.code, 2)
        self.assertIn("--org-id", stderr.getvalue())

    def test_existing_ios_xe_network_is_reused(self):
        api = mock.Mock()
        api.get.side_effect = [
            [
                {
                    "id": "N_1",
                    "name": "network1",
                    "productTypes": ["appliance"],
                    "details": [
                        {
                            "productType": "appliance",
                            "name": "operating system",
                            "value": "IOS XE",
                        }
                    ],
                }
            ],
            [{"serial": "Q4QA-GQ5H-2U74"}],
            [],
            [{"serial": "Q4QA-GQ5H-2U74"}],
        ]

        result = onboard_row(api, "123", self.row())

        self.assertEqual(result["networkId"], "N_1")
        api.post.assert_called_once_with(
            "/networks/N_1/devices/claim?addAtomically=true",
            {
                "serials": ["Q4QA-GQ5H-2U74"],
                "detailsByDevice": [
                    {
                        "serial": "Q4QA-GQ5H-2U74",
                        "details": [{"name": "device mode", "value": "managed"}],
                    }
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
