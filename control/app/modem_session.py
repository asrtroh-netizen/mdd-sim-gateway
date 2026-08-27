"""Version-tolerant ModemManager session helpers.

Cellular SMS create used to go through ``mmcli --messaging-create-sms-with-text``.
That flag does not exist on ModemManager 1.20 (Ubuntu 22.04's apt package), so
``mmcli`` reports ``error: no actions specified`` and every send fails.

This module owns the create step through D-Bus ``Messaging.Create``. Each property
is a separate argument, so the body never touches a shell or mmcli's comma-separated
key/value parser. Later QMI or in-process MM bindings can replace the busctl transport
without changing callers.

Send, listing and data-bearer toggles keep using flags that already exist on 1.20
(``--send``, ``--enable``, ``--disable``).
"""
from __future__ import annotations

import re
import subprocess

SMS_PATH_RE = re.compile(r"/org/freedesktop/ModemManager1/SMS/\d+")
BUSCTL_OBJECT_RE = re.compile(
    r'(?:^|\s)o\s+"(/org/freedesktop/ModemManager1/SMS/\d+)"'
    r'|(/org/freedesktop/ModemManager1/SMS/\d+)')


def _invoke(args: list[str], runner, timeout: float):
    try:
        result = runner(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "unavailable"
    except Exception:
        return None, "error"
    return result, None


def _sms_path_from_busctl(result) -> str:
    text = str(getattr(result, "stdout", "") or "")
    match = BUSCTL_OBJECT_RE.search(text)
    if not match:
        match = SMS_PATH_RE.search(text)
        return match.group(0) if match and SMS_PATH_RE.fullmatch(match.group(0)) else ""
    path = match.group(1) or match.group(2) or ""
    return path if SMS_PATH_RE.fullmatch(path) else ""


def create_text_sms(modem_path: str, recipient: str, text: str, runner=subprocess.run,
                    *, timeout: float = 30.0) -> tuple[str, str | None, object | None]:
    """Create a text SMS via D-Bus Messaging.Create.

    Returns ``(sms_path, problem, result)``. ``problem`` is a stable token
    (``timeout``, ``unavailable``, ``error``) or None when the command ran.
    An empty path with ``problem is None`` means the daemon answered but did not
    return a usable object path.
    """
    args = [
        "busctl", "--system", "call",
        "org.freedesktop.ModemManager1", str(modem_path),
        "org.freedesktop.ModemManager1.Modem.Messaging", "Create",
        "a{sv}", "2",
        "number", "s", str(recipient),
        "text", "s", str(text),
    ]
    result, problem = _invoke(args, runner, timeout)
    if problem:
        return "", problem, result
    if getattr(result, "returncode", 1):
        return "", None, result
    return _sms_path_from_busctl(result), None, result
