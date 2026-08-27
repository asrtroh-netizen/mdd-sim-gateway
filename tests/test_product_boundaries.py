import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from control.app import config


class ProductBoundaryTests(unittest.TestCase):
    def temp_config(self):
        temp = tempfile.TemporaryDirectory()
        paths = patch.multiple(
            config,
            DATA_DIR=temp.name,
            CONFIG_PATH=str(Path(temp.name) / "config.yaml"),
        )
        return temp, paths

    def write_local(self, root, **values):
        with open(Path(root) / "local.yaml", "w", encoding="utf-8") as handle:
            yaml.safe_dump(values, handle)

    def test_sixth_sim_line_is_refused_but_existing_lines_remain_editable(self):
        temp, paths = self.temp_config()
        with temp, paths:
            for iid in range(1, config.MAX_SIM_LINES + 1):
                config.upsert_instance({"id": str(iid), "name": f"SIM {iid}"})
            with self.assertRaises(config.LineLimitError):
                config.upsert_instance({"id": "6", "name": "SIM 6"})
            edited = config.upsert_instance({"id": "5", "name": "kept"})
            self.assertEqual(edited["name"], "kept")

    def test_stale_remote_controls_are_removed_on_load_and_save(self):
        temp, paths = self.temp_config()
        with temp, paths:
            config.save({
                "settings": {"telegram": {"commands": {"enabled": True}}},
                "instances": {"1": {"id": "1", "sip": {
                    "external": [{"username": "remote", "password": "secret"}]}}},
            })
            loaded = config.load()
            self.assertNotIn("commands", loaded["settings"]["telegram"])
            self.assertEqual(loaded["instances"]["1"]["sip"]["external"], [])

            saved = config.upsert_instance({"id": "1", "sip": {
                "external": [{"username": "remote", "password": "secret"}]}})
            self.assertEqual(saved["sip"]["external"], [])

    def test_retired_activation_event_is_removed_from_every_notification_channel(self):
        temp, paths = self.temp_config()
        with temp, paths:
            config.save({
                "settings": {key: {"events": {"activation_reminder": True}}
                             for key in ("webhook", "telegram", "pushplus")},
                "instances": {},
            })
            settings = config.load()["settings"]
            for key in ("webhook", "telegram", "pushplus"):
                self.assertNotIn("activation_reminder", settings[key]["events"])
                self.assertTrue(settings[key]["events"]["software_update"])

    def test_any_mcc_can_be_saved_on_a_line(self):
        """The gateway is not locked to MCC 460/461; any readable SIM can be a line."""
        temp, paths = self.temp_config()
        with temp, paths:
            for index, mcc in enumerate(("234", "310", "460", "461", "001"), start=1):
                saved = config.upsert_instance({
                    "id": str(index), "name": f"SIM {index}", "mcc": mcc, "mnc": "01",
                    "iccid": f"890000000000000000{index}",
                })
                self.assertEqual(saved["mcc"], mcc)

    def test_only_first_five_legacy_lines_are_startable(self):
        temp, paths = self.temp_config()
        with temp, paths:
            config.save({"instances": {
                str(iid): {"id": str(iid), "index": iid}
                for iid in range(1, 8)
            }})
            self.assertTrue(config.line_allowed("5"))
            self.assertFalse(config.line_allowed("6"))

    def test_local_yaml_raises_the_four_limits(self):
        temp, paths = self.temp_config()
        with temp, paths:
            self.write_local(temp.name, max_sim_lines=8, allow_external_sip=True,
                             allow_telegram_commands=True, persist_asterisk_debug=True)
            for iid in range(1, 8):
                config.upsert_instance({"id": str(iid), "name": f"SIM {iid}"})
            sixth = config.upsert_instance({"id": "8", "name": "SIM 8"})
            self.assertEqual(sixth["name"], "SIM 8")
            self.assertTrue(config.line_allowed("8"))

            config.save({
                "settings": {"telegram": {"commands": {"enabled": True}},
                             "debug": {"asterisk": True, "charon": True}},
                "instances": {"1": {"id": "1",
                                    "debug": {"asterisk": True, "charon": True},
                                    "sip": {"external": [
                                        {"username": "remote", "password": "secret"}]}}},
            })
            loaded = config.load()
            self.assertTrue(loaded["settings"]["telegram"]["commands"]["enabled"])
            self.assertEqual(loaded["instances"]["1"]["sip"]["external"][0]["username"],
                             "remote")
            self.assertTrue(loaded["instances"]["1"]["debug"]["asterisk"])
            saved = config.upsert_instance({
                "id": "1",
                "debug": {"asterisk": True},
                "sip": {"external": [{"username": "remote", "password": "secret"}]},
            })
            self.assertEqual(saved["sip"]["external"][0]["username"], "remote")
            self.assertTrue(saved["debug"]["asterisk"])
            rendered = config.render_instance_json({
                "id": "1", "imsi": "001010000000001", "mcc": "001", "mnc": "01",
                "imei": "123456789012345", "ami_secret": "secret",
                "sip": {"webrtc": {"password": "password"},
                        "external": [{"username": "desk", "password": "pw"}]},
                "debug": {"asterisk": True},
            }, {**config.DEFAULTS["settings"], "debug": {"asterisk": True}})
            self.assertEqual(rendered["sip"]["external"][0]["username"], "desk")
            self.assertTrue(rendered["debug"]["asterisk"])
            self.assertEqual(rendered["sip"]["user_agent"], "MDD-Sim-Gateway")

    def test_settings_api_save_writes_local_yaml_and_changes_runtime(self):
        """PUT /api/settings delegates to update_settings; a reload must show the same values."""
        temp, paths = self.temp_config()
        with temp, paths:
            settings = config.get_settings()
            self.assertEqual(settings["max_sim_lines"], 5)
            self.assertFalse(settings["allow_external_sip"])
            self.assertFalse(settings["allow_telegram_commands"])
            self.assertFalse(settings["persist_asterisk_debug"])

            saved = config.update_settings({
                "timezone": "UTC",
                "max_sim_lines": 8,
                "allow_external_sip": True,
                "allow_telegram_commands": True,
                "persist_asterisk_debug": True,
            })
            self.assertEqual(saved["max_sim_lines"], 8)
            self.assertTrue(saved["allow_external_sip"])
            self.assertEqual(saved["timezone"], "UTC")
            self.assertTrue((Path(temp.name) / "local.yaml").is_file())
            disk = yaml.safe_load((Path(temp.name) / "local.yaml").read_text())
            self.assertEqual(disk["max_sim_lines"], 8)
            self.assertTrue(disk["allow_external_sip"])

            for iid in range(1, 7):
                config.upsert_instance({"id": str(iid), "name": f"SIM {iid}"})
            self.assertTrue(config.line_allowed("6"))
            persisted = config.upsert_instance({
                "id": "1",
                "sip": {"external": [{"username": "phone", "password": "secret"}]},
                "debug": {"asterisk": True},
            })
            self.assertEqual(persisted["sip"]["external"][0]["username"], "phone")
            self.assertTrue(persisted["debug"]["asterisk"])
            self.assertTrue(config.get_settings()["telegram"]["commands"]["enabled"])
            reloaded = config.get_settings()
            self.assertEqual(reloaded["max_sim_lines"], 8)
            self.assertTrue(reloaded["allow_external_sip"])
            self.assertTrue(reloaded["allow_telegram_commands"])
            self.assertTrue(reloaded["persist_asterisk_debug"])
            self.assertEqual(reloaded["timezone"], "UTC")

            dropped = config.prepare_settings_payload({
                "allow_telegram_commands": False,
                "telegram": {"commands": {"enabled": True}},
            })
            self.assertNotIn("commands", dropped["telegram"])
            kept = config.prepare_settings_payload({
                "allow_telegram_commands": True,
                "telegram": {"bot_token": "t", "chat_id": "1"},
            })
            self.assertTrue(kept["telegram"]["commands"]["enabled"])


if __name__ == "__main__":
    unittest.main()
