#!/usr/bin/env python3
"""Engine image architecture checks and leftover-tag cleanup.

Release assets and local builds are per-architecture. Installing an ARM64 Engine on
an amd64 host (upstream #13) is refused. After a successful update, unused prior
Engine tags are removed; cleanup fails closed if it would delete the running tag
(upstream #15).
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys


ENGINE_IMAGE = "mdd-sim-gateway/engine"
ENGINE_BASE_TAG = "mdd-sim-gateway/engine-base:trusted"
ENGINE_REPOSITORIES = (
    "mdd-sim-gateway/engine",
    "mdd-sim-gateway/engine-base",
    "ghcr.io/mddidd/mdd-sim-gateway-engine",
)
# One explicit rollback point plus the trusted overlay base. Neither is dangling.
PROTECTED_TAGS = frozenset({
    ENGINE_IMAGE,
    f"{ENGINE_IMAGE}:latest",
    f"{ENGINE_IMAGE}:previous",
    ENGINE_BASE_TAG,
})


class EngineImageError(RuntimeError):
    pass


def host_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    raise EngineImageError(f"unsupported CPU architecture: {machine or 'unknown'}")


def docker_arch_name(value: str) -> str:
    """Normalize Docker / uname architecture names to amd64 | arm64."""
    text = str(value or "").strip().lower()
    if text in {"aarch64", "arm64"}:
        return "arm64"
    if text in {"x86_64", "amd64"}:
        return "amd64"
    return text


def engine_archive_name(version: str, arch: str | None = None) -> str:
    return f"mdd-sim-gateway-engine-v{version}-{arch or host_arch()}.tar.gz"


def control_archive_name(version: str, arch: str | None = None) -> str:
    return f"mdd-sim-gateway-control-v{version}-{arch or host_arch()}.tar.gz"


def engine_registry_tag(registry_image: str, version: str, arch: str | None = None) -> str:
    return f"{registry_image}:v{version}-{arch or host_arch()}"


def _run(command: list[str], runner=subprocess.run) -> subprocess.CompletedProcess:
    return runner(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def inspect_image(image: str, fmt: str, *, runner=subprocess.run) -> str:
    checked = _run(["docker", "image", "inspect", image, "--format", fmt], runner)
    if checked.returncode != 0:
        return ""
    return (checked.stdout or "").strip()


def image_architecture(image: str, *, runner=subprocess.run) -> str:
    return docker_arch_name(inspect_image(image, "{{.Architecture}}", runner=runner))


def image_id(image: str, *, runner=subprocess.run) -> str:
    return inspect_image(image, "{{.Id}}", runner=runner)


def assert_image_matches_host(image: str, *, expected_arch: str | None = None,
                              runner=subprocess.run) -> str:
    """Refuse to activate an Engine whose Architecture does not match this host."""
    wanted = docker_arch_name(expected_arch or host_arch())
    actual = image_architecture(image, runner=runner)
    if not actual:
        raise EngineImageError(f"could not read architecture of engine image {image}")
    if actual != wanted:
        raise EngineImageError(
            f"refusing to install {actual} engine image {image} on {wanted} host")
    return actual


def _is_engine_repository(repository: str) -> bool:
    name = str(repository or "").strip()
    if name in ENGINE_REPOSITORIES:
        return True
    return any(name == repo or name.startswith(repo + "/") for repo in ENGINE_REPOSITORIES)


def list_engine_images(*, runner=subprocess.run) -> list[dict]:
    """Return local Engine images as {id, repository, tag, ref}."""
    listed = _run(
        ["docker", "images", "--no-trunc", "--format",
         "{{.ID}}\t{{.Repository}}\t{{.Tag}}"],
        runner)
    if listed.returncode != 0:
        raise EngineImageError(
            f"could not list engine images: {(listed.stderr or listed.stdout or '').strip()}")
    images = []
    for line in (listed.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        image_id_value, repository, tag = (part.strip() for part in parts)
        if not image_id_value or not _is_engine_repository(repository):
            continue
        ref = repository if tag in {"", "<none>"} else f"{repository}:{tag}"
        images.append({
            "id": image_id_value,
            "repository": repository,
            "tag": tag,
            "ref": ref,
        })
    return images


def running_engine_image_ids(*, runner=subprocess.run,
                             name_prefix: str = "mdd-sim-gateway-engine-") -> set[str]:
    """Image IDs of currently running Engine containers. Fail closed if the list is unreadable."""
    listed = _run(
        ["docker", "ps", "--no-trunc", "--format", "{{.ID}}\t{{.Image}}\t{{.Names}}"],
        runner)
    if listed.returncode != 0:
        raise EngineImageError(
            f"could not list running engine containers: "
            f"{(listed.stderr or listed.stdout or '').strip() or 'docker ps failed'}")
    running: set[str] = set()
    for line in (listed.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        container_id, image_ref, name = (part.strip() for part in parts[:3])
        if not name.startswith(name_prefix) and not _is_engine_repository(image_ref.split(":")[0]):
            continue
        inspected = _run(
            ["docker", "inspect", container_id, "--format", "{{.Image}}"],
            runner)
        if inspected.returncode != 0 or not (inspected.stdout or "").strip():
            raise EngineImageError(
                f"could not inspect running engine container {name or container_id}")
        running.add(inspected.stdout.strip())
    return running


def cleanup_unused_engine_images(
    *,
    current_image: str = ENGINE_IMAGE,
    keep_previous: bool = True,
    runner=subprocess.run,
) -> dict:
    """Remove unused prior Engine tags after a successful update.

    Protected: the live ``mdd-sim-gateway/engine`` tag, its ``:previous`` rollback
    (when ``keep_previous``), the trusted overlay base, and every image ID still
    used by a running Engine container. If a candidate for deletion is the
    running image, cleanup raises instead of deleting it.
    """
    current_id = image_id(current_image, runner=runner)
    if not current_id:
        raise EngineImageError(
            f"refusing engine image cleanup: current tag {current_image} is missing")
    running_ids = running_engine_image_ids(runner=runner)
    protected_ids = {current_id, *running_ids}
    protected_refs = {current_image, f"{current_image}:latest"}
    if keep_previous:
        previous_ref = f"{current_image}:previous"
        previous_id = image_id(previous_ref, runner=runner)
        if previous_id:
            protected_ids.add(previous_id)
            protected_refs.add(previous_ref)
    base_id = image_id(ENGINE_BASE_TAG, runner=runner)
    if base_id:
        protected_ids.add(base_id)
        protected_refs.add(ENGINE_BASE_TAG)

    images = list_engine_images(runner=runner)
    removed: list[str] = []
    kept: list[str] = []
    for item in images:
        ref = item["ref"]
        if ref in PROTECTED_TAGS or ref in protected_refs or item["id"] in protected_ids:
            kept.append(ref)
            continue
        if item["id"] in running_ids or item["id"] == current_id:
            raise EngineImageError(
                f"refusing to delete engine tag {ref}: it is the running image")
        deleted = _run(["docker", "rmi", ref], runner)
        if deleted.returncode != 0:
            detail = (deleted.stderr or deleted.stdout or "docker rmi failed").strip()
            raise EngineImageError(f"could not remove unused engine tag {ref}: {detail}")
        removed.append(ref)
    return {
        "ok": True,
        "removed": removed,
        "kept": sorted(set(kept)),
        "current_id": current_id,
        "running_ids": sorted(running_ids),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Engine image arch checks and leftover cleanup")
    parser.add_argument("command", choices=("arch", "check", "cleanup"))
    parser.add_argument("--image", default=ENGINE_IMAGE)
    args = parser.parse_args(argv)
    try:
        if args.command == "arch":
            print(host_arch())
            return 0
        if args.command == "check":
            actual = assert_image_matches_host(args.image)
            print(f"{args.image} architecture {actual} matches host {host_arch()}")
            return 0
        result = cleanup_unused_engine_images(current_image=args.image)
        print(f"removed {len(result['removed'])} unused engine tag(s)")
        for ref in result["removed"]:
            print(f"  - {ref}")
        return 0
    except EngineImageError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
