"""The P4 main.py split is a move: handlers stay importable from control.app.main."""
import unittest

from control.app import main
from control.app.routers import devices, engineering, esim, lines, sms, carrier_profiles


class RouterSplitTests(unittest.TestCase):
    def test_handlers_remain_on_main_for_existing_tests(self):
        self.assertIs(main.api_devices, devices.api_devices)
        self.assertIs(main.api_device_capabilities, devices.api_device_capabilities)
        self.assertIs(main.api_device_at, engineering.api_device_at)
        self.assertIs(main.api_instance_start, lines.api_instance_start)
        self.assertIs(main.send_sms_on_line, sms.send_sms_on_line)
        self.assertIs(main.api_sms_send, sms.api_sms_send)
        self.assertIs(main.api_esim_chip, esim.api_esim_chip)
        self.assertIs(main.api_esim_enable, esim.api_esim_enable)
        self.assertIs(main.api_carrier_profiles, carrier_profiles.api_carrier_profiles)

    def test_extracted_paths_are_registered_once(self):
        paths = list(main.app.openapi()["paths"])
        for path in (
            "/api/devices",
            "/api/devices/{device_id}/at",
            "/api/devices/{device_id}/capabilities",
            "/api/instances",
            "/api/instances/{iid}/start",
            "/api/instances/{iid}/sms/send",
            "/api/esim/chip",
            "/api/carrier-profiles",
        ):
            self.assertEqual(paths.count(path), 1, path)


if __name__ == "__main__":
    unittest.main()
