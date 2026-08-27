"""doctor CLI: mocked missing and present host tools. Never prints secrets."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from control.app import doctor


def _which_map(present):
    def which(name):
        return f"/usr/bin/{name}" if name in present else None
    return which


def _runner(results):
    """results: command-tuple prefix -> CompletedProcess."""
    def run(command, **_kwargs):
        key = tuple(command[:3])
        if key in results:
            return results[key]
        key2 = tuple(command[:2])
        if key2 in results:
            return results[key2]
        return SimpleNamespace(returncode=1, stdout="", stderr="missing")
    return run


class DoctorTests(unittest.TestCase):
    def test_reports_every_missing_piece(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = doctor.collect_checks(
                which=_which_map(set()),
                runner=_runner({}),
                data_dir=Path(tmp) / "missing-data")
        names = {item["name"]: item for item in checks}
        self.assertFalse(names["docker"]["ok"])
        self.assertIn("docker", names["docker"]["missing"])
        self.assertFalse(names["modemmanager"]["ok"])
        self.assertIn("mmcli", names["modemmanager"]["missing"])
        self.assertFalse(names["pcscd"]["ok"])
        self.assertFalse(names["data_dir"]["ok"])
        report = doctor.format_report(checks)
        self.assertIn("MISSING", report)
        self.assertIn("missing:", report)
        self.assertNotIn("secret", report.lower().split("missing:")[0] or "")

    def test_reports_present_tools_without_secrets(self):
        present = {"docker", "mmcli", "ModemManager", "pcscd"}
        ok = SimpleNamespace(returncode=0, stdout="28.0.0\n", stderr="")
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(doctor.Path, "is_socket", return_value=True), \
                patch.object(doctor.Path, "exists", return_value=True), \
                patch.dict(os.environ, {"MDD_TELEGRAM_TOKEN": "secret-token-value"}):
            checks = doctor.collect_checks(
                which=_which_map(present),
                runner=_runner({
                    ("docker", "info"): ok,
                    ("docker", "version"): ok,
                    ("mmcli", "-L"): SimpleNamespace(
                        returncode=0, stdout="/org/freedesktop/ModemManager1/Modem/0\n",
                        stderr=""),
                    ("docker", "image"): SimpleNamespace(
                        returncode=0, stdout="amd64\n", stderr=""),
                }),
                data_dir=Path(tmp))
            report = doctor.format_report(checks)
        self.assertTrue(all(item["ok"] for item in checks), checks)
        self.assertIn("all checks passed", report)
        self.assertNotIn("secret-token-value", report)

    def test_engine_arch_mismatch_is_a_missing_piece(self):
        present = {"docker"}
        with patch.object(doctor, "host_arch", return_value="amd64"):
            checks = doctor.collect_checks(
                which=_which_map(present),
                runner=_runner({
                    ("docker", "info"): SimpleNamespace(returncode=0, stdout="", stderr=""),
                    ("docker", "version"): SimpleNamespace(returncode=0, stdout="1\n", stderr=""),
                    ("docker", "image"): SimpleNamespace(
                        returncode=0, stdout="arm64\n", stderr=""),
                }),
                data_dir=None)
        engine = next(item for item in checks if item["name"] == "engine_arch")
        self.assertFalse(engine["ok"])
        self.assertIn("amd64 engine image", engine["missing"])

    def test_cli_exits_nonzero_when_something_is_missing(self):
        with patch.object(doctor, "collect_checks", return_value=[
                {"name": "docker", "ok": False, "detail": "no", "missing": ["docker"]}]):
            self.assertEqual(doctor.main([]), 1)


if __name__ == "__main__":
    unittest.main()
