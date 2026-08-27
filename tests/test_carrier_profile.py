"""Carrier interoperability profiles and IPCC field mapping (no live carrier)."""
import io
import plistlib
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from control.app import carrier_ipcc, carrier_profile, config, egress, main, store


EXAMPLE_LAB = Path("examples/carrier-profiles/001-01-test-lab.yaml")
EXAMPLE_UNUSED = Path("examples/carrier-profiles/234-99-unused-plmn.yaml")


class CarrierProfileSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patch = patch.multiple(
            config,
            DATA_DIR=str(root),
            CONFIG_PATH=str(root / "config.yaml"),
        )
        self.patch.start()
        carrier_profile.reset_cache()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(carrier_profile.reset_cache)

    def _install(self, src: Path):
        dest = Path(config.DATA_DIR) / "carrier-profiles"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest / src.name)
        carrier_profile.reset_cache()

    def test_example_lab_profile_loads_and_applies(self):
        self._install(EXAMPLE_LAB)
        hit = carrier_profile.match("001", "01")
        self.assertEqual(hit["id"], "example-test-lab")
        self.assertEqual(hit["epdg"], "epdg.example.test")
        rendered = config.render_instance_json({
            "id": "1", "index": 0, "imsi": "001010000000000",
            "mcc": "001", "mnc": "01", "iccid": "8900000000000000001",
            "imei": "490154203237518", "ami_secret": "test-secret",
            "sip": {"webrtc": {"enable": True, "password": "test-password"}},
        }, {})
        self.assertEqual(rendered["epdg"], "epdg.example.test")
        self.assertEqual(rendered["realm"], "ims.example.test")
        self.assertEqual(rendered["smsc"], "+447700900111")
        self.assertEqual(rendered["cp_mode_order"], "v6,dual,v4")
        self.assertIn("country=GB", rendered["sip"]["pani"])
        self.assertNotIn("ffffffffffff", rendered["sip"]["pani"])
        self.assertEqual(egress.epdg_for({
            "mcc": "001", "mnc": "01"}), "epdg.example.test")

    def test_explicit_line_fields_win_over_the_profile(self):
        self._install(EXAMPLE_LAB)
        rendered = config.render_instance_json({
            "id": "1", "index": 0, "imsi": "001010000000000",
            "mcc": "001", "mnc": "01", "epdg": "epdg.manual.test",
            "smsc": "+447700900222",
            "imei": "490154203237518", "ami_secret": "test-secret",
            "sip": {"webrtc": {"enable": True, "password": "test-password"}},
        }, {})
        self.assertEqual(rendered["epdg"], "epdg.manual.test")
        self.assertEqual(rendered["smsc"], "+447700900222")

    def test_unmatched_plmn_keeps_imsi_derived_epdg(self):
        self._install(EXAMPLE_LAB)
        self.assertIsNone(carrier_profile.match("310", "260"))
        self.assertEqual(
            egress.epdg_for({"mcc": "310", "mnc": "260"}),
            "epdg.epc.mnc260.mcc310.pub.3gppnetwork.org")
        self.assertEqual(config.carrier_sip_defaults("310", "260", "test-card"), {})

    def test_unknown_carrier_without_a_file_does_not_invent_sip(self):
        self.assertEqual(config.carrier_sip_defaults("001", "01", "test-card"), {})

    def test_unused_plmn_pins_ipv4_first_and_placeholder_pani(self):
        self._install(EXAMPLE_UNUSED)
        self.assertEqual(config.cp_mode_order_for("234", "99"), "v4,dual,v6")
        sip = config.carrier_sip_defaults("234", "99", "test-card")
        self.assertIn("ffffffffffff", sip["pani"])
        self.assertIn("country=GB", sip["pani"])

    def test_builtin_giffgaff_hint_still_applies_without_a_file(self):
        first = config.carrier_sip_defaults("234", "10", "test-card")
        self.assertIn("country=GB", first["pani"])
        self.assertNotIn("ffffffffffff", first["pani"])
        self.assertTrue(first["user_eq_phone"])
        self.assertIn("dual", config.cp_mode_order_for("234", "15").split(",")[0])

    def test_secret_aka_fields_are_rejected(self):
        with self.assertRaises(carrier_profile.ProfileError):
            carrier_profile.normalize_profile({
                "id": "bad", "matches": [{"mcc": "001", "mnc": "01"}],
                "overrides": {"opc": "00"},
            })


class CarrierIpccImportTests(unittest.TestCase):
    def test_plist_zip_maps_only_used_fields(self):
        document = {
            "MCC": "001",
            "MNC": "01",
            "ePDGHostname": "epdg.example.test",
            "IMSRealm": "ims.example.test",
            "SMSC": "+447700900111",
            "APN": "ims",
            "IgnoredHandsetKey": "do-not-copy",
            "IMS": {"PrefersIPv6": True},
        }
        raw = plistlib.dumps(document, fmt=plistlib.FMT_XML)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Payload/Test.bundle/carrier.plist", raw)
        profile = carrier_ipcc.profile_from_ipcc(buffer.getvalue(), profile_id="from-ipcc")
        self.assertEqual(profile["id"], "from-ipcc")
        self.assertEqual(profile["matches"], [{"mcc": "001", "mnc": "01"}])
        self.assertEqual(profile["epdg"], "epdg.example.test")
        self.assertEqual(profile["realm"], "ims.example.test")
        self.assertEqual(profile["smsc"], "+447700900111")
        self.assertEqual(profile["apn"], "ims")
        self.assertEqual(profile["ims_af"], "v6")
        self.assertEqual(profile["source"]["kind"], "ipcc")
        self.assertNotIn("IgnoredHandsetKey", profile)

    def test_ipcc_with_ki_is_rejected(self):
        raw = plistlib.dumps({"MCC": "001", "MNC": "01", "Ki": "00"},
                             fmt=plistlib.FMT_XML)
        with self.assertRaises(carrier_profile.ProfileError):
            carrier_ipcc.profile_from_ipcc(raw)


class SmsRouteVisibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patch = patch.multiple(
            store, DATA_DIR=str(root),
            DB_PATH=str(root / "mdd-sim-gateway.sqlite"),
            PREVIOUS_DB_PATH=str(root / "vowifi.sqlite"))
        self.patch.start()
        store.init()

    async def asyncTearDown(self):
        main.hub.sms_send_locks.clear()
        self.patch.stop()
        self.temp.cleanup()

    async def test_failed_ims_path_is_recorded_and_not_silent(self):
        ami = Mock(connected=True)
        ami.registration_state = AsyncMock(return_value="Registered")
        ami.send_sms = AsyncMock(return_value={"ok": False})
        with patch.object(main.hub, "ami_for", new=AsyncMock(return_value=ami)), \
                patch.object(main.store, "add_message",
                             return_value={"id": 9, "status": "pending"}), \
                patch.object(main.store, "set_message_status"), \
                patch.object(main.hub, "broadcast", new=AsyncMock()):
            result = await main.send_sms_on_line("3", "+447700900333", "hello", "auto")
        self.assertFalse(result["ok"])
        self.assertEqual(result["transport"], "vowifi")
        self.assertTrue(result["error"])
        route = store.last_sms_route("3")
        self.assertEqual(route["transport"], "vowifi")
        self.assertFalse(route["ok"])
        self.assertTrue(route["error"])


if __name__ == "__main__":
    unittest.main()
