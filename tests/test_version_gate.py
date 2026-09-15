import unittest

from cisco_device_onboard.device_setup_engine import (
    _running_version,
    _version_at_least,
)


class VersionGateTests(unittest.TestCase):
    def test_extracts_iosxe_version(self):
        self.assertEqual(_running_version("Cisco IOS XE Software, Version 26.2.1"), "26.2.1")

    def test_skips_equal_or_newer_versions(self):
        self.assertTrue(_version_at_least("26.2.1", "26.2"))
        self.assertTrue(_version_at_least("27.1", "26.2"))
        self.assertFalse(_version_at_least("26.1.9", "26.2"))
        self.assertFalse(_version_at_least(None, "26.2"))


if __name__ == "__main__":
    unittest.main()
