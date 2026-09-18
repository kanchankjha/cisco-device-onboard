import io
import csv
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cisco_device_onboard import console_upgrade
from cisco_device_onboard.console_upgrade import upgrade_row
from cisco_device_onboard.csv_input import DeviceRow


class FakeProcess:
    def __init__(self, output, return_code=0):
        self.stdout = io.StringIO(output)
        self.returncode = return_code
        self.wait_called = False

    def wait(self):
        self.wait_called = True
        return self.returncode


class ConsoleUpgradeOutputTests(unittest.TestCase):
    def test_upgrade_row_streams_engine_output(self):
        process = FakeProcess("first log line\nsecond log line\n")
        row = DeviceRow(
            network_name="",
            serial="AAAA-BBBB-CCCC",
            console_ip="192.0.2.10",
            console_port=8235,
            extra={},
            row_number=2,
        )
        config = {
            "device": {"connection": {}},
            "image": {"source": {"server": {}}},
        }
        args = SimpleNamespace()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = io.StringIO()
            with mock.patch("cisco_device_onboard.console_upgrade.subprocess.Popen", return_value=process) as popen:
                with redirect_stdout(output):
                    result = upgrade_row(row, config, Path(temp_dir), args)
            log_path = Path(temp_dir) / "AAAA-BBBB-CCCC.log"
            json_path = Path(temp_dir) / "AAAA-BBBB-CCCC.json"
            self.assertTrue(log_path.is_file())
            self.assertFalse(json_path.exists())

        log_output = output.getvalue()
        self.assertIn("device=AAAA-BBBB-CCCC", log_output)
        self.assertIn("network=-", log_output)
        self.assertIn("first log line", log_output)
        self.assertIn("second log line", log_output)
        self.assertIn("END result=PASSED", log_output)
        self.assertTrue(process.wait_called)
        self.assertEqual(result["result"], "PASSED")
        popen_args, popen_kwargs = popen.call_args
        command = popen_args[0]
        self.assertEqual(command[1], "-u")
        self.assertEqual(popen_kwargs["stderr"], subprocess.STDOUT)

    def test_write_summary_csv_uses_input_order_and_pass_fail_status(self):
        results = [
            {"row": 3, "serial": "DDDD-EEEE-FFFF", "result": "FAILED"},
            {"row": 2, "serial": "AAAA-BBBB-CCCC", "result": "PASSED"},
            {"row": 4, "serial": "GGGG-HHHH-IIII", "result": "skipped"},
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = console_upgrade._write_summary_csv(Path(temp_dir), results)
            with summary_path.open(newline="", encoding="utf-8") as summary_file:
                rows = list(csv.reader(summary_file))

        self.assertEqual(
            rows,
            [
                ["appliance-serial", "status"],
                ["AAAA-BBBB-CCCC", "PASSED"],
                ["DDDD-EEEE-FFFF", "FAILED"],
                ["GGGG-HHHH-IIII", "PASSED"],
            ],
        )

    def test_main_runs_devices_in_bounded_batches(self):
        rows = [
            DeviceRow(
                network_name="network1",
                serial=f"AAAA-BBBB-{index:04d}",
                console_ip=f"192.0.2.{index}",
                console_port=8235,
                extra={},
                row_number=index + 1,
            )
            for index in range(1, 6)
        ]
        config = {
            "target_version": "26.2",
            "image": {
                "source": {
                    "path": "/images/cat9k.bin",
                    "server": {"ip": "192.0.2.20"},
                }
            },
            "wan_interfaces": ["Te0/0/8"],
            "timeouts": {},
        }
        events = []
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def fake_upgrade_row(*args, **kwargs):
            nonlocal active, maximum_active
            row = args[0]
            batch_number = kwargs["batch_number"]
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                events.append(("start", row.serial, batch_number))
            time.sleep(0.02)
            with lock:
                events.append(("end", row.serial, batch_number))
                active -= 1
            return {
                "serial": row.serial,
                "row": row.row_number,
                "result": "PASSED",
                "batch_number": batch_number,
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            output = io.StringIO()
            with mock.patch.object(console_upgrade, "load_devices", return_value=rows), mock.patch.object(
                console_upgrade, "load_upgrade_config", return_value=config
            ), mock.patch.object(console_upgrade, "upgrade_row", side_effect=fake_upgrade_row):
                with redirect_stdout(output):
                    result = console_upgrade.main(
                        [
                            "--csv",
                            "devices.csv",
                            "--config",
                            "device_upgrade.yaml",
                            "--report-dir",
                            temp_dir,
                            "--batch-size",
                            "2",
                        ]
                    )
            summary_path = Path(temp_dir) / "console-upgrade-summary.csv"
            self.assertTrue(summary_path.is_file())

        self.assertEqual(result, 0)
        self.assertEqual(maximum_active, 2)
        first_batch_end = max(index for index, event in enumerate(events) if event[2] == 1 and event[0] == "end")
        second_batch_start = min(index for index, event in enumerate(events) if event[2] == 2 and event[0] == "start")
        self.assertLess(first_batch_end, second_batch_start)
        self.assertIn("BATCH 1/3 START", output.getvalue())
        self.assertIn("BATCH 3/3 END", output.getvalue())
        self.assertIn("Summary CSV:", output.getvalue())

    def test_build_device_config_applies_jump_host_environment_overrides(self):
        row = DeviceRow(
            network_name="network1",
            serial="AAAA-BBBB-CCCC",
            console_ip="192.0.2.10",
            console_port=8403,
            extra={},
            row_number=2,
        )
        base_config = {
            "device": {"connection": {"proxy": True}},
            "jump_host": {"ip": "172.29.3.64", "port": 2023},
        }
        args = SimpleNamespace(
            console_protocol="telnet",
            console_username="",
            console_password="",
            console_password_env="CONSOLE_PASSWORD",
            enable_password="",
            enable_password_env="ENABLE_PASSWORD",
        )

        with mock.patch.dict(
            "os.environ",
            {"JUMP_HOST_USERNAME": "meraki", "JUMP_HOST_PASSWORD": "jump-pass"},
            clear=False,
        ):
            config = console_upgrade.build_device_config(base_config, row, args)

        self.assertEqual(config["jump_host"]["username"], "meraki")
        self.assertEqual(config["jump_host"]["password"], "jump-pass")


if __name__ == "__main__":
    unittest.main()
