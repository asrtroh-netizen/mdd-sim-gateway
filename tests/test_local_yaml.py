import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from control.app import config


class LocalYamlLoaderTests(unittest.TestCase):
    def temp_paths(self):
        temp = tempfile.TemporaryDirectory()
        paths = patch.multiple(
            config,
            DATA_DIR=temp.name,
            CONFIG_PATH=str(Path(temp.name) / "config.yaml"),
        )
        return temp, paths

    def test_missing_file_keeps_product_defaults(self):
        temp, paths = self.temp_paths()
        with temp, paths:
            flags = config.load_local()
            self.assertEqual(flags["max_sim_lines"], 5)
            self.assertFalse(flags["allow_external_sip"])
            self.assertFalse(flags["allow_telegram_commands"])
            self.assertFalse(flags["persist_asterisk_debug"])
            settings = config.get_settings()
            self.assertEqual(settings["max_sim_lines"], 5)
            self.assertFalse(settings["allow_external_sip"])

    def test_bad_types_fall_back_to_defaults(self):
        temp, paths = self.temp_paths()
        with temp, paths:
            Path(temp.name).mkdir(parents=True, exist_ok=True)
            (Path(temp.name) / "local.yaml").write_text(
                "max_sim_lines: not-a-number\n"
                "allow_external_sip: maybe\n"
                "allow_telegram_commands: []\n"
                "persist_asterisk_debug: {}\n",
                encoding="utf-8")
            flags = config.load_local()
            self.assertEqual(flags["max_sim_lines"], config.DEFAULT_MAX_SIM_LINES)
            self.assertFalse(flags["allow_external_sip"])
            self.assertFalse(flags["allow_telegram_commands"])
            self.assertFalse(flags["persist_asterisk_debug"])

    def test_ceiling_clamps_line_cap(self):
        temp, paths = self.temp_paths()
        with temp, paths:
            (Path(temp.name) / "local.yaml").write_text(
                "max_sim_lines: 100\n", encoding="utf-8")
            self.assertEqual(config.load_local()["max_sim_lines"],
                             config.ABSOLUTE_MAX_SIM_LINES)
            (Path(temp.name) / "local.yaml").write_text(
                "max_sim_lines: 0\n", encoding="utf-8")
            self.assertEqual(config.load_local()["max_sim_lines"],
                             config.DEFAULT_MAX_SIM_LINES)

    def test_unreadable_or_non_mapping_file_is_ignored(self):
        temp, paths = self.temp_paths()
        with temp, paths:
            (Path(temp.name) / "local.yaml").write_text("- just a list\n", encoding="utf-8")
            flags = config.load_local()
            self.assertEqual(flags["max_sim_lines"], 5)
            self.assertFalse(flags["allow_external_sip"])

    def test_save_local_round_trips_and_is_private(self):
        temp, paths = self.temp_paths()
        with temp, paths:
            saved = config.save_local({
                "max_sim_lines": 12,
                "allow_external_sip": True,
                "allow_telegram_commands": "yes",
                "persist_asterisk_debug": 1,
            })
            self.assertEqual(saved["max_sim_lines"], 12)
            self.assertTrue(saved["allow_external_sip"])
            path = Path(temp.name) / "local.yaml"
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            disk = yaml.safe_load(path.read_text())
            self.assertEqual(disk["max_sim_lines"], 12)

    def test_install_sh_seeds_example_only_when_missing(self):
        script = Path(__file__).resolve().parent.parent / "install.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("seed_local_yaml()", text)
        self.assertIn('if [ ! -f "$MDD_DATA_DIR/local.yaml" ]', text)
        self.assertIn('examples/local.yaml', text)
        example = Path(__file__).resolve().parent.parent / "examples" / "local.yaml"
        data = yaml.safe_load(example.read_text())
        self.assertGreaterEqual(data["max_sim_lines"], 5)
        self.assertTrue(data["allow_external_sip"])
        self.assertTrue(data["allow_telegram_commands"])
        self.assertTrue(data["persist_asterisk_debug"])


if __name__ == "__main__":
    unittest.main()
