import csv
import tempfile
import unittest
from pathlib import Path

from cisco_device_onboard.csv_input import CsvInputError, load_devices
from cisco_device_onboard.upgrade_config import _normalize_yaml, load_upgrade_config


class InputTests(unittest.TestCase):
    @staticmethod
    def remove_temp_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def write_csv(self, text: str) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(self.remove_temp_file, Path(handle.name))
        return Path(handle.name)

    def test_console_csv_requires_console_columns(self):
        path = self.write_csv("network-name,appliance-serial-number\nHome,AAAA-BBBB-CCCC\n")
        with self.assertRaises(CsvInputError):
            load_devices(path, require_console=True)

    def test_onboarding_csv_can_omit_console_columns(self):
        path = self.write_csv("network-name,appliance-serial-number\nHome,AAAA-BBBB-CCCC\n")
        rows = load_devices(path, require_network=True)
        self.assertEqual(rows[0].network_name, "Home")
        self.assertIsNone(rows[0].console_port)

    def test_extra_columns_are_preserved(self):
        path = self.write_csv(
            "network-name,appliance-serial-number,console-ip,console-port,location\n"
            "Home,AAAA-BBBB-CCCC,192.0.2.10,8235,lab\n"
        )
        rows = load_devices(path, require_console=True)
        self.assertEqual(rows[0].extra["location"], "lab")

    def test_normalizer_removes_editor_line_numbers(self):
        self.assertEqual(_normalize_yaml("1 target_version: '26.2'\n2 ~\n3 image:\n"), "target_version: '26.2'\nimage:")

    def test_upgrade_config_accepts_single_wan_interface(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        handle.write(
            """
target_version: "26.2"
image:
  source:
    path: /images/cat9k.bin
    server:
      ip: 192.0.2.20
      username: image-user
      password: image-password
wan_interfaces:
  - Te0/0/8
"""
        )
        handle.close()
        self.addCleanup(self.remove_temp_file, Path(handle.name))

        config = load_upgrade_config(Path(handle.name))

        self.assertEqual(config["wan_interfaces"], ["Te0/0/8"])


if __name__ == "__main__":
    unittest.main()
