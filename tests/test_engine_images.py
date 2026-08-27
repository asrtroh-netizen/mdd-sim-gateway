"""Engine image leftover cleanup and host-arch refuse (P4 / upstream #13, #15)."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from host import engine_images


def _completed(code=0, out="", err=""):
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class HostArchTests(unittest.TestCase):
    def test_normalizes_uname_and_docker_names(self):
        with patch.object(engine_images.platform, "machine", return_value="x86_64"):
            self.assertEqual(engine_images.host_arch(), "amd64")
        with patch.object(engine_images.platform, "machine", return_value="aarch64"):
            self.assertEqual(engine_images.host_arch(), "arm64")
        self.assertEqual(engine_images.docker_arch_name("x86_64"), "amd64")
        self.assertEqual(engine_images.engine_archive_name("9.9.9", "amd64"),
                         "mdd-sim-gateway-engine-v9.9.9-amd64.tar.gz")

    def test_wrong_arch_image_is_refused(self):
        with patch.object(engine_images, "host_arch", return_value="amd64"), \
                patch.object(engine_images, "inspect_image", return_value="arm64"):
            with self.assertRaises(engine_images.EngineImageError) as raised:
                engine_images.assert_image_matches_host("mdd-sim-gateway/engine")
            self.assertIn("arm64", str(raised.exception))
            self.assertIn("amd64", str(raised.exception))


class CleanupTests(unittest.TestCase):
    def test_unused_prior_tags_are_removed_and_running_tag_is_protected(self):
        current_id = "sha256:current"
        previous_id = "sha256:previous"
        stale_id = "sha256:stale"
        images = [
            f"{current_id}\tmdd-sim-gateway/engine\tlatest",
            f"{previous_id}\tmdd-sim-gateway/engine\tprevious",
            f"{stale_id}\tmdd-sim-gateway/engine\tv1.4.1",
            f"{stale_id}\tghcr.io/mddidd/mdd-sim-gateway-engine\tv1.4.1",
        ]
        removed = []

        def runner(command, **_kwargs):
            if command[:3] == ["docker", "image", "inspect"]:
                target, fmt = command[3], command[command.index("--format") + 1]
                if target == "mdd-sim-gateway/engine" and "{{.Id}}" in fmt:
                    return _completed(0, current_id + "\n")
                if target.endswith(":previous") and "{{.Id}}" in fmt:
                    return _completed(0, previous_id + "\n")
                if target.endswith("engine-base:trusted"):
                    return _completed(1, "")
                return _completed(1, "")
            if command[:2] == ["docker", "images"]:
                return _completed(0, "\n".join(images) + "\n")
            if command[:2] == ["docker", "ps"]:
                return _completed(0, "ctr1\tmdd-sim-gateway/engine\tmdd-sim-gateway-engine-1\n")
            if command[:2] == ["docker", "inspect"]:
                return _completed(0, current_id + "\n")
            if command[:2] == ["docker", "rmi"]:
                removed.append(command[2])
                return _completed(0, "")
            return _completed(1, "", "unexpected")

        result = engine_images.cleanup_unused_engine_images(runner=runner)
        self.assertTrue(result["ok"])
        self.assertIn("mdd-sim-gateway/engine:v1.4.1", result["removed"])
        self.assertIn("ghcr.io/mddidd/mdd-sim-gateway-engine:v1.4.1", result["removed"])
        self.assertNotIn("mdd-sim-gateway/engine", result["removed"])
        self.assertNotIn("mdd-sim-gateway/engine:previous", result["removed"])
        self.assertEqual(sorted(removed), sorted(result["removed"]))

    def test_cleanup_fails_closed_when_the_running_tag_would_be_deleted(self):
        running_id = "sha256:live"

        def runner(command, **_kwargs):
            if command[:3] == ["docker", "image", "inspect"]:
                return _completed(0, running_id + "\n")
            if command[:2] == ["docker", "images"]:
                return _completed(
                    0, f"{running_id}\tmdd-sim-gateway/engine\tv1.4.0-old\n")
            if command[:2] == ["docker", "ps"]:
                return _completed(0, "ctr\tmdd-sim-gateway/engine\tmdd-sim-gateway-engine-1\n")
            if command[:2] == ["docker", "inspect"]:
                return _completed(0, running_id + "\n")
            if command[:2] == ["docker", "rmi"]:
                self.fail("must not docker rmi the running engine image")
            return _completed(1, "", "unexpected")

        with self.assertRaises(engine_images.EngineImageError) as raised:
            engine_images.cleanup_unused_engine_images(runner=runner)
        self.assertIn("running image", str(raised.exception))

    def test_cleanup_fails_closed_when_docker_ps_is_unreadable(self):
        def runner(command, **_kwargs):
            if command[:3] == ["docker", "image", "inspect"]:
                return _completed(0, "sha256:current\n")
            if command[:2] == ["docker", "ps"]:
                return _completed(1, "", "cannot talk to docker")
            return _completed(1, "", "unexpected")

        with self.assertRaises(engine_images.EngineImageError) as raised:
            engine_images.cleanup_unused_engine_images(runner=runner)
        self.assertIn("running engine", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
