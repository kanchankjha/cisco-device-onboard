import unittest
from unittest import mock

from cisco_device_onboard.device_setup_engine import (
    ConsoleCredentialError,
    ConsoleDisconnectedError,
    _running_version,
    _version_at_least,
    run_upgrade,
)


class AlreadyCurrentSession:
    def __init__(self, config):
        self.config = config
        self.closed = False

    def connect(self):
        pass

    def execute(self, command, timeout=120):
        if command == "show version":
            return "Cisco IOS XE Software, Version 26.2.1"
        raise AssertionError(f"Unexpected command: {command}")

    def configure_wan_dhcp(self, config):
        return {"show_ip_interface_brief": "Te0/0/8 192.0.2.2 up up\nTe0/0/9 192.0.2.3 up up"}

    def copy_image(self, config, image_name):
        raise AssertionError("copy_image must not run when version is already current")

    def install_image(self, config, image_name):
        raise AssertionError("install_image must not run when version is already current")

    def configure_cloud_management(self, config):
        return {"service_cloud_mgmt": "service cloud-mgmt connect"}

    def check_internet_connectivity(self, config):
        return {"reachable": True, "active_wan_interfaces": ["Te0/0/8"]}

    def close(self):
        self.closed = True


class VersionGateTests(unittest.TestCase):
    def test_extracts_iosxe_version(self):
        self.assertEqual(_running_version("Cisco IOS XE Software, Version 26.2.1"), "26.2.1")

    def test_skips_equal_or_newer_versions(self):
        self.assertTrue(_version_at_least("26.2.1", "26.2"))
        self.assertTrue(_version_at_least("27.1", "26.2"))
        self.assertFalse(_version_at_least("26.1.9", "26.2"))
        self.assertFalse(_version_at_least(None, "26.2"))

    def test_run_upgrade_does_not_copy_or_install_current_version(self):
        config = {
            "device": {"name": "router", "connection": {}},
            "protocol": "telnet",
            "target_version": "26.2",
            "wan_interfaces": ["Te0/0/8", "Te0/0/9"],
            "image": {
                "destination": "bootflash:",
                "source": {
                    "path": "/images/cat9k.bin",
                    "protocol": "scp",
                    "server": {"ip": "192.0.2.20", "username": "image-user", "password": "image-password"},
                },
            },
        }

        with mock.patch("cisco_device_onboard.device_setup_engine.ConsoleSession", AlreadyCurrentSession):
            report = run_upgrade(config)

        self.assertEqual(report["result"], "PASSED")
        self.assertEqual(report["upgrade_status"], "SKIPPED")
        self.assertNotIn("install_output", report)

    def test_run_upgrade_retries_console_disconnect_three_times_at_five_seconds(self):
        config = {
            "device": {"name": "router", "connection": {}},
            "protocol": "telnet",
            "target_version": "26.2",
            "wan_interfaces": ["Te0/0/8", "Te0/0/9"],
            "image": {
                "destination": "bootflash:",
                "source": {
                    "path": "/images/cat9k.bin",
                    "protocol": "scp",
                    "server": {"ip": "192.0.2.20", "username": "image-user", "password": "image-password"},
                },
            },
        }
        session = mock.Mock()
        session.connect.side_effect = [
            ConsoleDisconnectedError("link dropped"),
            ConsoleDisconnectedError("link dropped"),
            None,
        ]
        session.execute.return_value = "Cisco IOS XE Software, Version 26.2.1"
        session.configure_wan_dhcp.return_value = {
            "show_ip_interface_brief": "Te0/0/8 192.0.2.2 up up"
        }
        session.configure_cloud_management.return_value = {
            "service_cloud_mgmt": "service cloud-mgmt connect"
        }

        with mock.patch(
            "cisco_device_onboard.device_setup_engine.ConsoleSession",
            return_value=session,
        ), mock.patch("cisco_device_onboard.device_setup_engine.time.sleep") as sleep:
            report = run_upgrade(config)

        self.assertEqual(report["result"], "PASSED")
        self.assertEqual(report["console_retry_count"], 2)
        self.assertEqual(session.connect.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(5), mock.call(5)])

    def test_run_upgrade_reports_credentials_without_retrying(self):
        config = {
            "device": {"name": "router", "connection": {}},
            "protocol": "telnet",
            "target_version": "26.2",
            "wan_interfaces": ["Te0/0/8"],
            "image": {
                "destination": "bootflash:",
                "source": {
                    "path": "/images/cat9k.bin",
                    "protocol": "scp",
                    "server": {"ip": "192.0.2.20", "username": "image-user", "password": "image-password"},
                },
            },
        }
        session = mock.Mock()
        session.connect.side_effect = ConsoleCredentialError(
            "Console authentication failed for the supplied and predefined credentials"
        )

        with mock.patch(
            "cisco_device_onboard.device_setup_engine.ConsoleSession",
            return_value=session,
        ), mock.patch("cisco_device_onboard.device_setup_engine.time.sleep") as sleep:
            report = run_upgrade(config)

        self.assertEqual(report["result"], "FAILED")
        self.assertEqual(report["failure_category"], "CONSOLE_CREDENTIALS")
        self.assertIn("restart console-upgrade", report["recommended_action"])
        self.assertEqual(session.connect.call_count, 1)
        sleep.assert_not_called()

    def test_run_upgrade_retries_package_verification_after_deleting_image(self):
        image_name = "cat9k-universalk9.26.2.1.SPA.bin"
        config = {
            "device": {"name": "router", "connection": {}},
            "protocol": "telnet",
            "target_version": "26.2",
            "wan_interfaces": ["Te0/0/8"],
            "timeouts": {"reboot": 60, "poll_interval": 1},
            "image": {
                "destination": "bootflash:",
                "source": {
                    "path": f"/images/{image_name}",
                    "protocol": "scp",
                    "server": {"ip": "192.0.2.20", "username": "image-user", "password": "image-password"},
                },
            },
        }
        session = mock.Mock()
        session.execute.side_effect = [
            "Cisco IOS XE Software, Version 26.1.1",
            f"System image file is \"bootflash:{image_name}\"",
        ]
        session.configure_wan_dhcp.return_value = {"active_wan_interfaces": ["Te0/0/8"]}
        session.check_image_server_reachability.return_value = {"reachable": True}
        session.install_image.side_effect = [
            "R0 FAILED: Install package verification fail",
            "R0 FAILED: Install package verification fail",
            "install add activate commit: SUCCESS",
        ]
        session.configure_cloud_management.return_value = {"cloud_mgmt_connect": "ok"}

        with mock.patch(
            "cisco_device_onboard.device_setup_engine.ConsoleSession",
            return_value=session,
        ):
            report = run_upgrade(config)

        self.assertEqual(report["result"], "PASSED")
        self.assertEqual(report["package_verification_retry_count"], 2)
        self.assertEqual(session.copy_image.call_count, 3)
        self.assertEqual(session.install_image.call_count, 3)
        self.assertEqual(session.delete_image.call_count, 2)
        self.assertEqual(len(report["install_attempts"]), 3)
        self.assertTrue(report["install_attempts"][0]["package_verification_failed"])
        self.assertFalse(report["install_attempts"][2]["package_verification_failed"])

    def test_run_upgrade_fails_after_three_package_verification_retries(self):
        image_name = "cat9k-universalk9.26.2.1.SPA.bin"
        config = {
            "device": {"name": "router", "connection": {}},
            "protocol": "telnet",
            "target_version": "26.2",
            "wan_interfaces": ["Te0/0/8"],
            "timeouts": {"reboot": 60, "poll_interval": 1},
            "image": {
                "destination": "bootflash:",
                "source": {
                    "path": f"/images/{image_name}",
                    "protocol": "scp",
                    "server": {"ip": "192.0.2.20", "username": "image-user", "password": "image-password"},
                },
            },
        }
        session = mock.Mock()
        session.execute.return_value = "Cisco IOS XE Software, Version 26.1.1"
        session.configure_wan_dhcp.return_value = {"active_wan_interfaces": ["Te0/0/8"]}
        session.check_image_server_reachability.return_value = {"reachable": True}
        session.install_image.return_value = "R0 FAILED: Install package verification fail"

        with mock.patch(
            "cisco_device_onboard.device_setup_engine.ConsoleSession",
            return_value=session,
        ):
            report = run_upgrade(config)

        self.assertEqual(report["result"], "FAILED")
        self.assertIn("package verification failed after 4", report["error"])
        self.assertEqual(report["package_verification_retry_count"], 4)
        self.assertEqual(session.copy_image.call_count, 4)
        self.assertEqual(session.install_image.call_count, 4)
        self.assertEqual(session.delete_image.call_count, 3)
        session.configure_cloud_management.assert_not_called()


if __name__ == "__main__":
    unittest.main()
