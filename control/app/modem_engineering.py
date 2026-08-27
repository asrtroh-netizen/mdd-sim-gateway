"""Operator-facing modem engineering: AT, USSD, radio metrics, operators, USB net.

Commands go through ModemManager (`mmcli --command` / 3GPP actions) so this process does
not take the AT port away from the VoWiFi bridge. PIN, Ki/OP/OPc and message bodies are
never stored in history or written to logs.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import threading
import time

from . import cellular_sms, device_state, ussd

log = logging.getLogger("vowifi.modem_engineering")

AT_RE = re.compile(r"^AT[A-Z0-9+&%*=?,.\s\"'#;:_/-]{0,180}$", re.I)
SECRET_AT = re.compile(
    r"\+(?:CPIN|CLCK|CPWD|CAMM)\b|(?:\b|\+)(?:KI|OP|OPC)\b|\+CMGS\b|\+CMGW\b|\+CMSS\b",
    re.I,
)
HEX_BLOB = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{24,}(?![0-9A-Fa-f])")
QUOTED = re.compile(r'"([^"]*)"')
CUSD_RE = re.compile(
    r'\+CUSD:\s*(\d+)(?:\s*,\s*"((?:[^"\\]|\\.)*)"(?:\s*,\s*(\d+))?)?',
    re.I | re.S,
)
COPS_LIST_RE = re.compile(
    r'\((\d+),"([^"]*)","([^"]*)","(\d{5,6})"(?:,([^)]*))?\)',
)
CFUN_RE = re.compile(r"\+CFUN:\s*(\d+)", re.I)
QCFG_USBNET_RE = re.compile(r'\+QCFG:\s*"usbnet"\s*,\s*(\d+)', re.I)
USBNET_NAME = {0: "qmi", 1: "ecm", 2: "mbim", 3: "rndis"}
USBNET_CODE = {name: code for code, name in USBNET_NAME.items()}
USSD_CODE_RE = re.compile(r"^[*#][*#0-9]{1,180}$")
PLMN_RE = re.compile(r"^\d{5,6}$")
HISTORY_LIMIT = 40
AT_TIMEOUT = 20.0
SCAN_TIMEOUT = 120.0
RESET_TIMEOUT = 90.0

_history_lock = threading.RLock()
_history: dict[str, list[dict]] = {}


def _history_path() -> str:
    return os.path.join(device_state.ROOT, "at-history.json")


def _load_history() -> dict[str, list[dict]]:
    try:
        with open(_history_path(), encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_history(value: dict[str, list[dict]]) -> None:
    os.makedirs(device_state.ROOT, exist_ok=True)
    path = _history_path()
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def redact_at(text: str) -> str:
    """Operator history may show the command shape, never PIN/key/SMS payloads."""
    value = str(text or "")
    if SECRET_AT.search(value):
        return "<redacted>"
    value = HEX_BLOB.sub("<redacted-secret>", value)
    def _quote(match):
        inner = match.group(1)
        if re.fullmatch(r"[*#0-9]+", inner) or re.fullmatch(r"\d{1,6}", inner):
            return match.group(0)
        if len(inner) >= 4:
            return '"<redacted>"'
        return match.group(0)
    return QUOTED.sub(_quote, value)


def validate_at(command: str) -> str:
    text = " ".join(str(command or "").split())
    if not text or not AT_RE.fullmatch(text):
        raise ValueError("AT command is empty or uses characters this terminal will not send")
    if SECRET_AT.search(text):
        raise ValueError("PIN, key-material and SMS-body commands are not sent from this terminal")
    return text


def validate_ussd(code: str) -> str:
    text = str(code or "").strip()
    if not USSD_CODE_RE.fullmatch(text):
        raise ValueError("USSD code must look like *100# or #225#")
    return text


def _result(ok=False, **extra) -> dict:
    payload = {"ok": bool(ok), "error": extra.pop("error", None)}
    payload.update(extra)
    return payload


def modem_path_for_device(device_id: str, status: dict | None = None) -> str:
    """Resolve the ModemManager object for a physical device id."""
    document = status if status is not None else device_state.status()
    device = (document.get("devices") or {}).get(str(device_id)) or {}
    path = str(device.get("mm_object") or "")
    return path if cellular_sms.MODEM_PATH_RE.search(path) else ""


def _invoke(args: list[str], runner, timeout: float):
    return cellular_sms._invoke(args, runner, timeout)


def send_at(modem_path: str, command: str, runner=subprocess.run,
            timeout: float = AT_TIMEOUT) -> dict:
    """Send one AT command through ModemManager and return the module reply."""
    try:
        command = validate_at(command)
    except ValueError as exc:
        return _result(error=str(exc), stage="validate", command="")
    result, problem = _invoke(
        ["-m", modem_path, f"--command={command}"], runner, timeout)
    if problem == "timeout":
        return _result(error="Timed out waiting for the module.", stage="command",
                       command=redact_at(command), uncertain=True)
    if problem == "unavailable":
        return _result(error="mmcli is not available on this host.", stage="command",
                       command=redact_at(command), unavailable=True)
    if problem or getattr(result, "returncode", 1):
        return _result(error=cellular_sms._command_error(
            result, "The module rejected the AT command."),
                       stage="command", command=redact_at(command))
    response = parse_at_response(getattr(result, "stdout", "") or "")
    # History and logs redact payloads. The live reply is returned so USSD/USB-net
    # parsers can read the module text; the AT API persists only the redacted copy.
    return _result(ok=True, stage="command", command=redact_at(command),
                   response=response, raw_ok=True)


def parse_at_response(stdout: str) -> str:
    """Pull the module payload out of mmcli --command text or JSON."""
    text = str(stdout or "")
    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        doc = {}
    if isinstance(doc, dict):
        modem = doc.get("modem") or {}
        for key in ("command-response", "at", "response"):
            value = modem.get(key) if isinstance(modem, dict) else None
            if value:
                return str(value).strip()
        value = doc.get("modem.generic.at-command-response") or doc.get("response")
        if value:
            return str(value).strip()
    match = re.search(r"(?im)^response:\s*(.*)$", text)
    if match:
        return match.group(1).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    skip = {"ok", "error"}
    kept = [line for line in lines if line.casefold() not in skip
            and not line.lower().startswith("successfully")]
    return "\n".join(kept).strip() or text.strip()


def record_history(device_id: str, command: str, response: str, *, ok: bool) -> list[dict]:
    entry = {
        "at": int(time.time()),
        "command": redact_at(command),
        "response": redact_at(response)[:500],
        "ok": bool(ok),
    }
    with _history_lock:
        stored = _history.get(device_id)
        if stored is None:
            stored = list(_load_history().get(device_id) or [])
        stored.append(entry)
        stored = stored[-HISTORY_LIMIT:]
        _history[device_id] = stored
        snapshot = _load_history()
        snapshot[device_id] = stored
        try:
            _save_history(snapshot)
        except OSError:
            log.warning("could not persist AT history for %s", device_id)
        return list(stored)


def history_for(device_id: str) -> list[dict]:
    with _history_lock:
        if device_id in _history:
            return list(_history[device_id])
        stored = list(_load_history().get(device_id) or [])
        _history[device_id] = stored
        return list(stored)


def parse_cusd(text: str) -> dict | None:
    """Parse +CUSD, 3GPP USSD XML, or a bare carrier string."""
    raw = str(text or "").strip()
    if not raw:
        return None
    match = CUSD_RE.search(raw)
    if match:
        quoted = (match.group(2) or "").replace('\\"', '"')
        parsed = ussd.parse(quoted) if quoted else None
        if parsed:
            parsed["status"] = int(match.group(1))
            return parsed
        text = " ".join(quoted.split())[:ussd.MAX_TEXT]
        if text:
            return {"text": text, "error_code": None, "language": "",
                    "status": int(match.group(1))}
        return None
    return ussd.parse(raw)


def send_ussd(modem_path: str, code: str, runner=subprocess.run,
              timeout: float = AT_TIMEOUT) -> dict:
    """Send a service code on the module (MM 3GPP USSD, then AT+CUSD)."""
    try:
        code = validate_ussd(code)
    except ValueError as exc:
        return _result(error=str(exc), stage="validate")
    result, problem = _invoke(
        ["-m", modem_path, f"--3gpp-ussd-initiate={code}"], runner, timeout)
    if problem == "timeout":
        return _result(error="Timed out waiting for the USSD reply.", stage="ussd",
                       uncertain=True)
    if not problem and result and result.returncode == 0:
        parsed = parse_cusd(getattr(result, "stdout", "") or "")
        if parsed:
            return _result(ok=True, stage="ussd", transport="cellular", **parsed)
        text = parse_at_response(getattr(result, "stdout", "") or "")
        parsed = parse_cusd(text) or ({"text": text[:ussd.MAX_TEXT]} if text else None)
        if parsed and parsed.get("text"):
            return _result(ok=True, stage="ussd", transport="cellular", **parsed)
    stderr = " ".join(str(getattr(result, "stderr", "") or "").split())
    if _unknown_mm_flag(stderr, "ussd"):
        return _ussd_via_at(modem_path, code, runner, timeout)
    if problem == "unavailable":
        return _result(error="mmcli is not available on this host.", stage="ussd",
                       unavailable=True)
    if problem or getattr(result, "returncode", 1):
        if _unknown_mm_flag(stderr, "ussd"):
            return _ussd_via_at(modem_path, code, runner, timeout)
        return _result(error=cellular_sms._command_error(
            result, "The module rejected the USSD request."), stage="ussd")
    return _result(error="The module returned no USSD text.", stage="ussd")


def _unknown_mm_flag(stderr: str, token: str) -> bool:
    text = (stderr or "").casefold()
    return ("unknown option" in text or "no actions specified" in text
            or "unrecognized option" in text or token in text and "unknown" in text)


def _ussd_via_at(modem_path: str, code: str, runner, timeout: float) -> dict:
    command = f'AT+CUSD=1,"{code}",15'
    sent = send_at(modem_path, command, runner=runner, timeout=timeout)
    if not sent.get("ok"):
        sent["stage"] = "ussd"
        return sent
    parsed = parse_cusd(sent.get("response") or "")
    if not parsed or not parsed.get("text"):
        return _result(error="The module returned no USSD text.", stage="ussd")
    return _result(ok=True, stage="ussd", transport="cellular", **parsed)


def _float_or_none(value) -> float | None:
    if value in (None, "", "--", "unknown", "n/a", "none"):
        return None
    try:
        number = float(str(value).split()[0])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def parse_radio_metrics(detail: str = "", signal: str = "") -> dict:
    """Extract RSRP/RSRQ/SINR, RAT, band and channel from mmcli keyvalue text."""
    text = "\n".join(part for part in (detail, signal) if part)

    def kv(key: str) -> str:
        return _kv(text, key)

    access = (kv("modem.generic.access-technologies")
              or kv("modem.generic.access-technologies.value[1]")
              or kv("modem.3gpp.access-technologies"))
    if not access:
        techs = re.findall(
            r"modem\.generic\.access-technologies\.value\[\d+\]\s*:\s*(\S+)", text)
        access = techs[0] if techs else ""
    band = (kv("modem.generic.current-bands.value[1]")
            or kv("modem.generic.current-bands")
            or "")
    if band.casefold() in {"--", "unknown", "none", "n/a"}:
        band = ""
    channel = None
    for key in ("modem.signal.lte.earfcn", "modem.signal.nr5g.earfcn",
                "modem.signal.umts.uarfcn", "modem.signal.gsm.arfcn"):
        raw = kv(key)
        if raw.isdigit():
            channel = int(raw)
            break
    family = "lte"
    if "nr5g" in text and kv("modem.signal.nr5g.rsrp"):
        family = "nr5g"
    elif kv("modem.signal.umts.rscp"):
        family = "umts"
    elif kv("modem.signal.gsm.rssi") and not kv("modem.signal.lte.rsrp"):
        family = "gsm"
    rsrp = _float_or_none(kv(f"modem.signal.{family}.rsrp") or kv("modem.signal.lte.rsrp"))
    rsrq = _float_or_none(kv(f"modem.signal.{family}.rsrq") or kv("modem.signal.lte.rsrq"))
    sinr = _float_or_none(kv(f"modem.signal.{family}.snr") or kv("modem.signal.lte.snr")
                          or kv(f"modem.signal.{family}.sinr"))
    if access.casefold() in {"--", "unknown", "none", "n/a"}:
        access = family if rsrp is not None else ""
    return {
        "rsrp": rsrp, "rsrq": rsrq, "sinr": sinr,
        "access_tech": access, "band": band, "channel": channel,
    }


def _kv(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}\s*:\s*(.*?)\s*$", text or "", re.MULTILINE)
    return match.group(1).strip() if match else ""


def parse_operator_scan(text: str) -> list[dict]:
    """Parse mmcli --3gpp-scan or AT+COPS=? into operator rows."""
    raw = str(text or "")
    operators = []
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        doc = {}
    networks = []
    if isinstance(doc, dict):
        modem = doc.get("modem") or {}
        gpp = modem.get("3gpp") if isinstance(modem, dict) else {}
        networks = (gpp or {}).get("scan-networks") or (gpp or {}).get("networks") or []
        if isinstance(networks, dict):
            networks = list(networks.values())
    for item in networks:
        if not isinstance(item, dict):
            continue
        operators.append({
            "plmn": str(item.get("operator-code") or item.get("code") or ""),
            "name": str(item.get("operator-name") or item.get("name") or ""),
            "access_tech": str(item.get("access-tech") or item.get("act") or ""),
            "availability": str(item.get("status") or item.get("availability") or ""),
        })
    if operators:
        return [row for row in operators if row["plmn"] or row["name"]]
    for match in COPS_LIST_RE.finditer(raw):
        stat, long_name, short_name, plmn, act = match.groups()
        availability = {"0": "unknown", "1": "available", "2": "current",
                        "3": "forbidden"}.get(stat, stat)
        operators.append({
            "plmn": plmn, "name": long_name or short_name,
            "access_tech": (act or "").strip(), "availability": availability,
        })
    if operators:
        return operators
    for line in raw.splitlines():
        match = re.search(
            r"(\d{5,6})\s*-\s*(.+?)\s*\(([^,]+),\s*([^)]+)\)", line)
        if match:
            operators.append({
                "plmn": match.group(1), "name": match.group(2).strip(),
                "access_tech": match.group(3).strip(),
                "availability": match.group(4).strip(),
            })
    return operators


def scan_operators(modem_path: str, runner=subprocess.run,
                   timeout: float = SCAN_TIMEOUT) -> dict:
    result, problem = _invoke(["-m", modem_path, "--3gpp-scan"], runner, timeout)
    if problem == "timeout":
        return _result(error="Network scan timed out.", stage="scan", uncertain=True)
    if problem == "unavailable":
        return _result(error="mmcli is not available on this host.", stage="scan",
                       unavailable=True)
    stderr = " ".join(str(getattr(result, "stderr", "") or "").split())
    if problem or getattr(result, "returncode", 1) or _unknown_mm_flag(stderr, "scan"):
        sent = send_at(modem_path, "AT+COPS=?", runner=runner, timeout=timeout)
        if not sent.get("ok"):
            return _result(error=sent.get("error") or "Network scan failed.", stage="scan")
        rows = parse_operator_scan(sent.get("response") or "")
        return _result(ok=True, stage="scan", operators=rows)
    rows = parse_operator_scan(getattr(result, "stdout", "") or "")
    return _result(ok=True, stage="scan", operators=rows)


def select_operator(modem_path: str, *, mode: str, plmn: str = "",
                    runner=subprocess.run, timeout: float = AT_TIMEOUT) -> dict:
    choice = str(mode or "").strip().casefold()
    if choice not in {"auto", "automatic", "manual", "home"}:
        return _result(error="Operator mode must be auto or manual.", stage="validate")
    if choice in {"auto", "automatic", "home"}:
        result, problem = _invoke(
            ["-m", modem_path, "--3gpp-register-home"], runner, timeout)
        if problem or getattr(result, "returncode", 1):
            sent = send_at(modem_path, "AT+COPS=0", runner=runner, timeout=timeout)
            if not sent.get("ok"):
                return _result(error=sent.get("error") or "Automatic registration failed.",
                               stage="select")
        readback = current_operator(modem_path, runner=runner, timeout=timeout)
        return _result(ok=True, stage="select", mode="auto", **readback)
    code = str(plmn or "").strip()
    if not PLMN_RE.fullmatch(code):
        return _result(error="Manual selection needs a 5- or 6-digit PLMN.", stage="validate")
    result, problem = _invoke(
        ["-m", modem_path, f"--3gpp-register-in-operator={code}"], runner, timeout)
    if problem or getattr(result, "returncode", 1):
        sent = send_at(modem_path, f'AT+COPS=1,2,"{code}"', runner=runner, timeout=timeout)
        if not sent.get("ok"):
            return _result(error=sent.get("error") or "Manual registration failed.",
                           stage="select")
    readback = current_operator(modem_path, runner=runner, timeout=timeout)
    return _result(ok=True, stage="select", mode="manual", requested_plmn=code, **readback)


def current_operator(modem_path: str, runner=subprocess.run,
                     timeout: float = 10.0) -> dict:
    result, problem = _invoke(
        ["-m", modem_path, "--output-keyvalue"], runner, timeout)
    if problem or getattr(result, "returncode", 1):
        return {"plmn": "", "name": "", "selection": ""}
    text = getattr(result, "stdout", "") or ""
    name = _kv(text, "modem.3gpp.operator-name")
    plmn = _kv(text, "modem.3gpp.operator-code")
    if name.casefold() in {"--", "unknown", "none", "n/a"}:
        name = ""
    if plmn.casefold() in {"--", "unknown", "none", "n/a"}:
        plmn = ""
    sent = send_at(modem_path, "AT+COPS?", runner=runner, timeout=timeout)
    selection = "unknown"
    if sent.get("ok"):
        match = re.search(r"\+COPS:\s*(\d+)", sent.get("response") or "")
        if match:
            selection = {"0": "auto", "1": "manual", "2": "deregistered",
                         "3": "format-only", "4": "manual-auto"}.get(
                             match.group(1), match.group(1))
    return {"plmn": plmn, "name": name, "selection": selection}


def parse_cfun(text: str) -> bool | None:
    match = CFUN_RE.search(str(text or ""))
    if not match:
        return None
    return match.group(1) not in {"0", "4"}


def parse_usbnet(text: str) -> dict | None:
    match = QCFG_USBNET_RE.search(str(text or ""))
    if not match:
        return None
    code = int(match.group(1))
    return {"code": code, "name": USBNET_NAME.get(code, str(code))}


def usbnet_status(modem_path: str, runner=subprocess.run,
                  timeout: float = AT_TIMEOUT) -> dict:
    sent = send_at(modem_path, 'AT+QCFG="usbnet"', runner=runner, timeout=timeout)
    if not sent.get("ok"):
        return _result(error=sent.get("error") or "USB net query failed.",
                       stage="usbnet", supported=False)
    parsed = parse_usbnet(sent.get("response") or "")
    if not parsed:
        return _result(ok=True, stage="usbnet", supported=False,
                       error="This module does not report USB net composition.")
    return _result(ok=True, stage="usbnet", supported=True, usbnet=parsed,
                   actual=parsed)


def set_usbnet(modem_path: str, mode, runner=subprocess.run,
               timeout: float = AT_TIMEOUT) -> dict:
    if isinstance(mode, str) and mode.strip().casefold() in USBNET_CODE:
        code = USBNET_CODE[mode.strip().casefold()]
    else:
        try:
            code = int(mode)
        except (TypeError, ValueError):
            return _result(error="USB net mode must be qmi, ecm, mbim, rndis, or 0-3.",
                           stage="validate")
    if code not in USBNET_NAME:
        return _result(error="USB net mode must be qmi, ecm, mbim, rndis, or 0-3.",
                       stage="validate")
    sent = send_at(modem_path, f'AT+QCFG="usbnet",{code}', runner=runner, timeout=timeout)
    if not sent.get("ok"):
        return _result(error=sent.get("error") or "USB net change was rejected.",
                       stage="usbnet", requested={"code": code, "name": USBNET_NAME[code]})
    readback = usbnet_status(modem_path, runner=runner, timeout=timeout)
    actual = (readback.get("usbnet") or readback.get("actual") or {})
    matched = actual.get("code") == code
    return _result(ok=matched, stage="usbnet", supported=True,
                   requested={"code": code, "name": USBNET_NAME[code]},
                   actual=actual if actual else None,
                   error=None if matched else "Module reported a different USB net mode.")


def restart_modem(modem_path: str, runner=subprocess.run,
                  timeout: float = RESET_TIMEOUT,
                  sleeper=time.sleep) -> dict:
    """Reset the module, then read radio/USB-net state back. Never invent success."""
    result, problem = _invoke(["-m", modem_path, "--reset"], runner, min(timeout, 20))
    used = "mmcli-reset"
    if problem or getattr(result, "returncode", 1):
        sent = send_at(modem_path, "AT+CFUN=1,1", runner=runner, timeout=min(timeout, 15))
        if not sent.get("ok") and problem != "timeout":
            return _result(error=sent.get("error") or "Modem reset failed.",
                           stage="restart")
        used = "at-cfun"
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        sleeper(2)
        detail, again = _invoke(["-m", modem_path, "--output-keyvalue"], runner, 10)
        if again or getattr(detail, "returncode", 1):
            continue
        text = getattr(detail, "stdout", "") or ""
        state = _kv(text, "modem.generic.state").lower()
        power = _kv(text, "modem.generic.power-state").lower()
        radio = power == "on" and state not in {"disabled", "disabling", "failed", "unknown", ""}
        last = {"state": state, "powered": power == "on", "radio_enabled": radio}
        if state and state not in {"unknown", "failed"}:
            usb = usbnet_status(modem_path, runner=runner, timeout=10)
            return _result(ok=True, stage="restart", method=used, **last,
                           usbnet=usb.get("usbnet") if usb.get("ok") else None)
    if last:
        return _result(ok=False, stage="restart", method=used, **last,
                       error="The module reset, but it did not become ready in time.",
                       uncertain=True)
    return _result(error="The module did not come back after reset.",
                   stage="restart", method=used, uncertain=True)


def radio_from_snapshot(cellular: dict | None, actual: dict | None = None) -> bool | None:
    """Prefer the live module snapshot over a desired-state echo."""
    cell = cellular or {}
    if cell.get("available") and "radio_enabled" in cell:
        return bool(cell.get("radio_enabled"))
    if actual and actual.get("cellular_radio_enabled") is not None:
        return bool(actual.get("cellular_radio_enabled"))
    return None
