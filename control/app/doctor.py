"""Host readiness checks for MDD Sim Gateway.

``python -m control.app.doctor`` (or ``./install.sh doctor``) reports whether
Docker, ModemManager/mmcli, pcscd, TUN/XFRM, the Engine image architecture and
the data directory are usable. Output never includes secrets, tokens, ICCIDs or
file contents from the data dir.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


SECRET_ENV = (
    "MDD_TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN", "MDD_WEBHOOK_URL",
    "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy",
    "all_proxy", "MDD_PUSHPLUS_TOKEN", "AMI_SECRET", "MDD_ADMIN_PASSWORD",
)


def host_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "amd64"
    return machine or "unknown"


def _run(command: list[str], *, runner=subprocess.run, timeout: float = 8) -> subprocess.CompletedProcess:
    return runner(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  timeout=timeout)


def _which(name: str, *, which=shutil.which) -> str | None:
    return which(name)


def _redact(text: str) -> str:
    """Strip long digit runs and leftover credential-looking tokens from details."""
    import re
    cleaned = re.sub(r"(?i)(token|secret|password|proxy)=\S+", r"\1=[redacted]", str(text or ""))
    cleaned = re.sub(r"[0-9]{8,}([0-9]{4})", r"****\1", cleaned)
    return cleaned[:240]


def _check_docker(*, which=shutil.which, runner=subprocess.run) -> dict:
    if not _which("docker", which=which):
        return {"name": "docker", "ok": False, "detail": "docker binary not found",
                "missing": ["docker"]}
    try:
        info = _run(["docker", "info"], runner=runner)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": "docker", "ok": False,
                "detail": _redact(f"docker info failed: {exc}"), "missing": ["docker daemon"]}
    if info.returncode != 0:
        return {"name": "docker", "ok": False,
                "detail": "docker is installed but the daemon is not reachable",
                "missing": ["docker daemon"]}
    version = ""
    try:
        shown = _run(["docker", "version", "--format", "{{.Server.Version}}"], runner=runner)
        version = (shown.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"name": "docker", "ok": True,
            "detail": f"daemon reachable{f' ({version})' if version else ''}",
            "missing": []}


def _check_modemmanager(*, which=shutil.which, runner=subprocess.run) -> dict:
    missing = []
    if not _which("mmcli", which=which):
        missing.append("mmcli")
    if not (_which("ModemManager", which=which) or _which("modemmanager", which=which)):
        # mmcli is enough to talk to an already-running daemon.
        if "mmcli" in missing:
            missing.append("ModemManager")
    if missing:
        return {"name": "modemmanager", "ok": False,
                "detail": "ModemManager / mmcli not found (needed for 4G SMS, radio and QMI)",
                "missing": missing}
    try:
        listed = _run(["mmcli", "-L"], runner=runner)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": "modemmanager", "ok": False,
                "detail": _redact(f"mmcli -L failed: {exc}"), "missing": ["mmcli"]}
    if listed.returncode != 0:
        return {"name": "modemmanager", "ok": False,
                "detail": "mmcli is present but cannot talk to ModemManager",
                "missing": ["ModemManager service"]}
    return {"name": "modemmanager", "ok": True,
            "detail": "mmcli can reach ModemManager", "missing": []}


def _check_pcscd(*, which=shutil.which, runner=subprocess.run) -> dict:
    if not (_which("pcscd", which=which) or Path("/run/pcscd/pcscd.comm").is_socket()):
        return {"name": "pcscd", "ok": False,
                "detail": "pcscd binary and /run/pcscd/pcscd.comm are both missing",
                "missing": ["pcscd"]}
    socket_ok = Path("/run/pcscd/pcscd.comm").is_socket()
    if socket_ok:
        return {"name": "pcscd", "ok": True,
                "detail": "pcscd socket is present", "missing": []}
    return {"name": "pcscd", "ok": False,
            "detail": "pcscd is installed but /run/pcscd/pcscd.comm is not a socket",
            "missing": ["pcscd service"]}


def _check_tun_xfrm() -> dict:
    missing = []
    tun = Path("/dev/net/tun")
    if not tun.exists():
        missing.append("/dev/net/tun")
    xfrm = Path("/proc/net/xfrm_stat")
    if not xfrm.exists():
        missing.append("xfrm (CONFIG_XFRM)")
    if missing:
        return {"name": "tun_xfrm", "ok": False,
                "detail": "kernel TUN/XFRM devices required by the SWu engine are missing",
                "missing": missing}
    return {"name": "tun_xfrm", "ok": True,
            "detail": "TUN device and XFRM are present", "missing": []}


def _check_engine_arch(*, which=shutil.which, runner=subprocess.run,
                       image: str = "mdd-sim-gateway/engine") -> dict:
    if not _which("docker", which=which):
        return {"name": "engine_arch", "ok": False,
                "detail": "cannot check engine image architecture without docker",
                "missing": ["docker"]}
    try:
        inspected = _run(
            ["docker", "image", "inspect", image, "--format", "{{.Architecture}}"],
            runner=runner)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": "engine_arch", "ok": False,
                "detail": _redact(f"engine image inspect failed: {exc}"),
                "missing": [image]}
    if inspected.returncode != 0:
        return {"name": "engine_arch", "ok": False,
                "detail": f"engine image {image} is not loaded; build it for {host_arch()}",
                "missing": [image]}
    actual = (inspected.stdout or "").strip().lower()
    if actual in {"aarch64", "arm64"}:
        actual = "arm64"
    elif actual in {"x86_64", "amd64"}:
        actual = "amd64"
    wanted = host_arch()
    if actual != wanted:
        return {"name": "engine_arch", "ok": False,
                "detail": f"engine image is {actual} but this host is {wanted}",
                "missing": [f"{wanted} engine image"]}
    return {"name": "engine_arch", "ok": True,
            "detail": f"engine image architecture {actual} matches host",
            "missing": []}


def _data_dir() -> Path:
    env = os.environ.get("MDD_DATA") or os.environ.get("MDD_DATA_DIR")
    if env:
        return Path(env)
    state = Path("/etc/mdd-sim-gateway/data-dir")
    if state.is_file():
        try:
            first = state.read_text(encoding="utf-8").splitlines()[0].strip()
            if first.startswith("/"):
                return Path(first)
        except OSError:
            pass
    repo = Path(__file__).resolve().parents[2]
    return repo / "data"


def _check_data_dir(path: Path | None = None) -> dict:
    root = path or _data_dir()
    if not root.exists():
        return {"name": "data_dir", "ok": False,
                "detail": "data directory does not exist yet (run install.sh install)",
                "missing": ["data directory"]}
    if not root.is_dir():
        return {"name": "data_dir", "ok": False,
                "detail": "data path exists but is not a directory",
                "missing": ["data directory"]}
    if not os.access(root, os.R_OK | os.W_OK):
        return {"name": "data_dir", "ok": False,
                "detail": "data directory is not writable",
                "missing": ["data directory permissions"]}
    return {"name": "data_dir", "ok": True,
            "detail": "data directory is present and writable",
            "missing": []}


def collect_checks(*, which=shutil.which, runner=subprocess.run,
                   data_dir: Path | None = None) -> list[dict]:
    return [
        _check_docker(which=which, runner=runner),
        _check_modemmanager(which=which, runner=runner),
        _check_pcscd(which=which, runner=runner),
        _check_tun_xfrm(),
        _check_engine_arch(which=which, runner=runner),
        _check_data_dir(data_dir),
    ]


def format_report(checks: list[dict]) -> str:
    lines = [f"MDD Sim Gateway doctor  host={host_arch()}"]
    missing: list[str] = []
    for item in checks:
        mark = "ok" if item.get("ok") else "MISSING"
        lines.append(f"  [{mark}] {item['name']}: {_redact(str(item.get('detail') or ''))}")
        missing.extend(item.get("missing") or [])
    if missing:
        lines.append("missing:")
        for name in missing:
            lines.append(f"  - {name}")
    else:
        lines.append("all checks passed")
    text = "\n".join(lines) + "\n"
    for needle in SECRET_ENV:
        if needle in text and os.environ.get(needle):
            text = text.replace(os.environ[needle], "[redacted]")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check MDD Sim Gateway host prerequisites")
    parser.parse_args(argv)
    checks = collect_checks()
    sys.stdout.write(format_report(checks))
    return 0 if all(item.get("ok") for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
