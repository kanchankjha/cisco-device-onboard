import re
import unittest
from unittest import mock

from cisco_device_onboard import device_setup_engine
from cisco_device_onboard.device_setup_engine import (
    ConsoleCredentialError,
    ConsoleSession,
    UpgradeConfigError,
)
from cisco_device_onboard.upgrade_config import load_upgrade_config
import tempfile
from pathlib import Path


class FakePexpect:
    EOF = object()
    TIMEOUT = type("FakeTimeout", (Exception,), {})

    def __init__(self, child):
        self.child = child

    def spawn(self, command, args, encoding, timeout):
        return self.child


class FakeChild:
    def __init__(self, events):
        self.events = list(events)
        self.before = ""
        self.after = ""
        self.sent = []
        self.logfile_read = None

    def expect(self, patterns, timeout=30):
        if not self.events:
            raise AssertionError("No fake console events remain")
        event = self.events.pop(0)
        if isinstance(event, tuple):
            index, before, after = event
        else:
            index, before, after = event, "", ""
        self.before = before
        self.after = after
        return index

    def sendline(self, value):
        self.sent.append(("sendline", value))

    def send(self, value):
        self.sent.append(("send", value))

    def close(self, force=False):
        self.sent.append(("close", force))


class FakePexpectSequence(FakePexpect):
    def __init__(self, children):
        self.children = list(children)

    def spawn(self, command, args, encoding, timeout):
        if not self.children:
            raise AssertionError("No fake console children remain")
        return self.children.pop(0)


def _config(protocol="telnet"):
    return {
        "protocol": protocol,
        "device": {
            "connection": {
                "protocol": protocol,
                "ip": "192.0.2.10",
                "port": 8235,
                "username": "admin",
                "password": "console-password",
                "enable_password": "enable-password",
            }
        },
        "timeouts": {"prompt": 3},
    }


def _upgrade_config(transfer_protocol="scp", skip_if_present=False):
    return {
        "image": {
            "destination": "bootflash:",
            "skip_if_present": skip_if_present,
            "source": {
                "protocol": transfer_protocol,
                "path": "/images/cat9k.bin",
                "server": {
                    "ip": "192.0.2.20",
                    "username": "image-user",
                    "password": "image-password",
                },
            },
        },
        "timeouts": {"copy": 30},
    }


class ConsoleConnectionTests(unittest.TestCase):
    @staticmethod
    def remove_temp_file(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def set_fake_pexpect(self, child):
        previous_pexpect = device_setup_engine.pexpect
        device_setup_engine.pexpect = FakePexpect(child)
        self.addCleanup(setattr, device_setup_engine, "pexpect", previous_pexpect)

    def run_connect(self, events, protocol="telnet"):
        child = FakeChild(events)
        self.set_fake_pexpect(child)
        ConsoleSession(_config(protocol)).connect()
        return child.sent

    def test_username_password_return_and_prompt(self):
        sent = self.run_connect(
            [
                7,
                6,
                8,
                (11, "", "Router>"),
                (0, "", "Router>"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
            ]
        )
        self.assertIn(("sendline", "admin"), sent)
        self.assertIn(("sendline", "console-password"), sent)
        self.assertIn(("send", "\r"), sent)

    def test_telnet_retries_with_predefined_cloud_credentials(self):
        child = FakeChild(
            [
                7,
                6,
                7,
                6,
                (11, "", "Router>"),
                (0, "", "Router>"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
            ]
        )
        previous_pexpect = device_setup_engine.pexpect
        device_setup_engine.pexpect = FakePexpect(child)
        self.addCleanup(setattr, device_setup_engine, "pexpect", previous_pexpect)

        with mock.patch.dict(
            "os.environ", {"CONSOLE_FALLBACK_PASSWORD": "fallback-password"}, clear=False
        ):
            ConsoleSession(_config()).connect()

        sent_values = [value for kind, value in child.sent if kind == "sendline"]
        self.assertEqual(sent_values[:4], ["admin", "console-password", "miles", "fallback-password"])

    def test_ssh_reconnects_with_predefined_username_after_authentication_failure(self):
        first_child = FakeChild([(14, "Permission denied", "")])
        second_child = FakeChild(
            [
                6,
                (11, "", "Router>"),
                (0, "", "Router>"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
            ]
        )
        previous_pexpect = device_setup_engine.pexpect
        device_setup_engine.pexpect = FakePexpectSequence([first_child, second_child])
        self.addCleanup(setattr, device_setup_engine, "pexpect", previous_pexpect)

        with mock.patch.dict(
            "os.environ", {"CONSOLE_FALLBACK_PASSWORD": "fallback-password"}, clear=False
        ):
            ConsoleSession(_config("ssh")).connect()

        self.assertIn(("sendline", "fallback-password"), second_child.sent)

    def test_authentication_failure_is_reported_as_a_credential_error(self):
        first_child = FakeChild([(16, "Permission denied", "")])
        second_child = FakeChild([(16, "Authentication failed", "")])
        previous_pexpect = device_setup_engine.pexpect
        device_setup_engine.pexpect = FakePexpectSequence([first_child, second_child])
        self.addCleanup(setattr, device_setup_engine, "pexpect", previous_pexpect)

        with mock.patch.dict(
            "os.environ", {"CONSOLE_FALLBACK_PASSWORD": "fallback-password"}, clear=False
        ):
            with self.assertRaises(ConsoleCredentialError) as raised:
                ConsoleSession(_config("ssh")).connect()

        self.assertIn("authentication failed", str(raised.exception).lower())
        self.assertEqual(raised.exception.failure_category, "CONSOLE_CREDENTIALS")
        self.assertIn("restart console-upgrade", raised.exception.recommended_action)

    def test_skips_basic_setup_autoinstall_and_save_dialogs(self):
        sent = self.run_connect(
            [
                9,
                10,
                13,
                12,
                (11, "", "Router>"),
                (0, "", "Router>"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
            ]
        )
        self.assertEqual(
            [
                ("sendline", "no"),
                ("sendline", "yes"),
                ("sendline", "no"),
                ("sendline", "0"),
            ],
            [item for item in sent if item[0] == "sendline"][:4],
        )
        self.assertIn(("send", "\r"), sent)

    def test_quiet_ssh_console_gets_wakeup_enter(self):
        sent = self.run_connect(
            [
                15,
                (11, "", "Router>"),
                (0, "", "Router>"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
            ],
            protocol="ssh",
        )
        self.assertIn(("send", "\r"), sent)

    def test_initial_setup_generates_and_confirms_bootstrap_secret(self):
        config = _config()
        config["device"]["connection"].pop("enable_password")
        child = FakeChild(
            [
                9,
                1,
                2,
                3,
                4,
                12,
                (11, "", "Router>"),
                (0, "", "Router>"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
            ]
        )
        self.set_fake_pexpect(child)
        ConsoleSession(config).connect()

        sent_values = [value for kind, value in child.sent if kind == "sendline"]
        setup_secrets = sent_values[1:5]
        self.assertEqual(setup_secrets[0], setup_secrets[1])
        self.assertEqual(setup_secrets[0], setup_secrets[2])
        self.assertEqual(setup_secrets[2], setup_secrets[3])
        self.assertEqual(len(setup_secrets[0]), 12)
        self.assertTrue(any(character.isupper() for character in setup_secrets[0]))
        self.assertTrue(any(character.islower() for character in setup_secrets[0]))
        self.assertTrue(any(character.isdigit() for character in setup_secrets[0]))
        self.assertIn(("sendline", "no logging console"), child.sent)

    def test_initial_setup_replaces_invalid_configured_secret(self):
        child = FakeChild(
            [
                9,
                1,
                2,
                12,
                (11, "", "Router>"),
                (0, "", "Router>"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
            ]
        )
        self.set_fake_pexpect(child)
        ConsoleSession(_config()).connect()

        sent_values = [value for kind, value in child.sent if kind == "sendline"]
        generated = sent_values[1]
        self.assertEqual(sent_values[1], sent_values[2])
        self.assertEqual(generated, device_setup_engine.DEFAULT_BOOTSTRAP_SECRET)
        self.assertEqual(len(generated), 12)
        self.assertTrue(any(character.isupper() for character in generated))
        self.assertTrue(any(character.islower() for character in generated))
        self.assertTrue(any(character.isdigit() for character in generated))

    def test_initial_setup_replaces_generated_secret_after_invalid_input(self):
        config = _config()
        config["device"]["connection"].pop("enable_password")
        child = FakeChild(
            [
                9,
                1,
                5,
                1,
                2,
                12,
                (11, "", "Router>"),
                (0, "", "Router>"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
                (1, "", "Router#"),
            ]
        )
        self.set_fake_pexpect(child)
        ConsoleSession(config).connect()

        sent_values = [value for kind, value in child.sent if kind == "sendline"]
        self.assertEqual(sent_values[1], sent_values[2])

    def test_wan_dhcp_continues_with_interface_that_gets_an_address(self):
        child = FakeChild(
            [
                (0, "", "Router(config)#"),
                (0, "", "Router(config-if)#"),
                (0, "", "Router(config-if)#"),
                (0, "", "Router(config-if)#"),
                (0, "", "Router(config)#"),
                (0, "", "Router(config-if)#"),
                (0, "", "Router(config-if)#"),
                (0, "", "Router(config-if)#"),
                (0, "", "Router(config)#"),
                (0, "", "Router#"),
                (
                    0,
                    "Te0/0/8  192.0.2.2  YES DHCP  up  up\n"
                    "Te0/0/9  unassigned  YES DHCP  administratively down  down\n",
                    "Router#",
                ),
                (0, "ip address dhcp\n", "Router#"),
                (0, "[OK]\n", "Router#"),
            ]
        )
        session = ConsoleSession(_config())
        session.child = child

        with mock.patch.object(
            device_setup_engine.time, "monotonic", side_effect=[0, 1]
        ):
            verification = session.configure_wan_dhcp(
                {
                    "wan_interfaces": ["Te0/0/8", "Te0/0/9"],
                    "timeouts": {"wan_dhcp_grace": 0, "poll_interval": 1},
                }
            )

        self.assertEqual(verification["active_wan_interfaces"], ["Te0/0/8"])
        self.assertEqual(verification["unassigned_wan_interfaces"], ["Te0/0/9"])
        self.assertIn(
            ("sendline", "show running-config interface Te0/0/8"), child.sent
        )
        self.assertNotIn(
            ("sendline", "show running-config interface Te0/0/9"), child.sent
        )

    def test_image_server_ping_retries_until_reachable(self):
        child = FakeChild(
            [
                (0, "Success rate is 0 percent (0/1)", "Router#"),
                (0, "Success rate is 100 percent (1/1)", "Router#"),
            ]
        )
        self.set_fake_pexpect(child)
        session = ConsoleSession(_config())
        session.child = child

        with mock.patch.object(device_setup_engine.time, "sleep") as sleep:
            result = session.check_image_server_reachability(_upgrade_config())

        self.assertTrue(result["reachable"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(
            [value for kind, value in child.sent if kind == "sendline"],
            [
                "ping 192.0.2.20 timeout 2",
                "ping 192.0.2.20 timeout 2",
            ],
        )
        sleep.assert_called_once_with(5)

    def test_internet_connectivity_requires_up_wan_and_successful_ping(self):
        child = FakeChild(
            [
                (
                    0,
                    "GigabitEthernet0/0/0 192.0.2.2 YES DHCP up up\n"
                    "GigabitEthernet0/0/1 unassigned YES DHCP administratively down down",
                    "Router#",
                ),
                (0, "Success rate is 100 percent (1/1)", "Router#"),
            ]
        )
        self.set_fake_pexpect(child)
        session = ConsoleSession(_config())
        session.child = child

        result = session.check_internet_connectivity(
            {"wan_interfaces": ["GigabitEthernet0/0/0", "GigabitEthernet0/0/1"]}
        )

        self.assertTrue(result["reachable"])
        self.assertEqual(result["active_wan_interfaces"], ["GigabitEthernet0/0/0"])
        self.assertEqual(
            [value for kind, value in child.sent if kind == "sendline"],
            ["show ip int br", "ping 8.8.8.8 timeout 2"],
        )

    def test_internet_connectivity_waits_for_wan_interface_to_come_up(self):
        child = FakeChild(
            [
                (0, "GigabitEthernet0/0/0 unassigned YES DHCP down down", "Router#"),
                (0, "GigabitEthernet0/0/0 192.0.2.2 YES DHCP up up", "Router#"),
                (0, "Success rate is 100 percent (1/1)", "Router#"),
            ]
        )
        self.set_fake_pexpect(child)
        session = ConsoleSession(_config())
        session.child = child

        with mock.patch.object(device_setup_engine.time, "sleep") as sleep:
            result = session.check_internet_connectivity(
                {
                    "wan_interfaces": ["GigabitEthernet0/0/0"],
                    "timeouts": {
                        "wan_internet_grace": 300,
                        "wan_internet_poll_interval": 15,
                    },
                }
            )

        self.assertTrue(result["reachable"])
        self.assertEqual(result["attempts"], 2)
        sleep.assert_called_once_with(15)

    def test_repeated_setup_selection_stops_instead_of_sending_ios_command(self):
        child = FakeChild([12, 12])
        self.set_fake_pexpect(child)

        with self.assertRaisesRegex(RuntimeError, "reappeared"):
            ConsoleSession(_config()).connect()

        self.assertEqual(
            [item for item in child.sent if item[0] == "sendline"],
            [("sendline", "0")],
        )

    def test_first_boot_transcript_prompts_match_expected_patterns(self):
        self.assertIsNotNone(
            re.search(device_setup_engine.SETUP_SECRET_PATTERN, "Enter enable secret:")
        )
        self.assertIsNotNone(
            re.search(
                device_setup_engine.SETUP_SECRET_CONFIRM_PATTERN,
                "Confirm enable secret:",
            )
        )
        self.assertIsNotNone(
            re.search(
                device_setup_engine.SETUP_INVALID_PATTERN,
                "% Invalid input.Please try again",
            )
        )

    def test_copy_image_uses_scp_url_and_verifies_image(self):
        child = FakeChild(
            [
                1,
                5,
                6,
                7,
                (0, "cat9k.bin", "Router#"),
            ]
        )
        self.set_fake_pexpect(child)
        session = ConsoleSession(_config())
        session.child = child
        output = session.copy_image(_upgrade_config("scp"), "cat9k.bin")

        self.assertIn(
            ("sendline", "copy scp://image-user@192.0.2.20//images/cat9k.bin bootflash:cat9k.bin"),
            child.sent,
        )
        self.assertIn(("sendline", "image-password"), child.sent)
        self.assertEqual(output, "cat9k.bin")

    def test_copy_image_uses_ftp_url_and_answers_ftp_prompts(self):
        child = FakeChild(
            [
                3,
                2,
                1,
                4,
                5,
                7,
                (0, "cat9k.bin", "Router#"),
            ]
        )
        self.set_fake_pexpect(child)
        session = ConsoleSession(_config())
        session.child = child
        session.copy_image(_upgrade_config("ftp"), "cat9k.bin")

        self.assertIn(
            ("sendline", "copy ftp://image-user@192.0.2.20//images/cat9k.bin bootflash:cat9k.bin"),
            child.sent,
        )
        self.assertIn(("sendline", "192.0.2.20"), child.sent)
        self.assertIn(("sendline", "image-user"), child.sent)
        self.assertIn(("sendline", "image-password"), child.sent)
        self.assertIn(("sendline", "/images/cat9k.bin"), child.sent)

    def test_copy_image_skips_when_image_is_present(self):
        child = FakeChild([(0, "cat9k.bin", "Router#")])
        session = ConsoleSession(_config())
        session.child = child
        output = session.copy_image(_upgrade_config("ftp", skip_if_present=True), "cat9k.bin")

        self.assertEqual(output, "cat9k.bin")
        self.assertEqual(child.sent, [("sendline", "dir bootflash:cat9k.bin")])

    def test_copy_image_rejects_unknown_transfer_protocol(self):
        session = ConsoleSession(_config())
        session.child = FakeChild([])
        with self.assertRaises(UpgradeConfigError):
            session.copy_image(_upgrade_config("tftp"), "cat9k.bin")

    def test_upgrade_config_defaults_transfer_protocol_to_scp(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        handle.write(
            """
target_version: "26.2"
image:
  destination: "bootflash:"
  source:
    path: /images/cat9k.bin
    server:
      ip: 192.0.2.20
      username: image-user
      password: image-password
wan_interfaces:
  - Te0/0/8
  - Te0/0/9
"""
        )
        handle.close()
        self.addCleanup(self.remove_temp_file, Path(handle.name))

        config = load_upgrade_config(Path(handle.name))

        self.assertEqual(config["image"]["source"]["protocol"], "scp")

    def test_upgrade_config_rejects_unknown_transfer_protocol(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
        handle.write(
            """
target_version: "26.2"
image:
  source:
    protocol: tftp
    path: /images/cat9k.bin
    server:
      ip: 192.0.2.20
      username: image-user
      password: image-password
wan_interfaces:
  - Te0/0/8
  - Te0/0/9
"""
        )
        handle.close()
        self.addCleanup(self.remove_temp_file, Path(handle.name))

        with self.assertRaisesRegex(Exception, "image.source.protocol"):
            load_upgrade_config(Path(handle.name))


if __name__ == "__main__":
    unittest.main()
