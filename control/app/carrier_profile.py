"""Importable carrier interoperability profiles owned by this repository.

A profile may override ePDG host/realm, IMS address family, PANI country/BSSID
policy, SMSC, APN, IDr mode, and the IPv4/IPv6 probe order. Matching is MCC-MNC
(or an explicit per-line profile id). When nothing matches, the gateway keeps
the 3GPP IMSI-derived ePDG/realm and the existing in-tree hints.

This is not an Apple IPCC dump and not a VoCat/MengMengCode carrier database.
Secret AKA material (Ki/OP/OPc) is rejected if present.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from copy import deepcopy
from typing import Any

import yaml

log = logging.getLogger("vowifi.carrier_profile")

SCHEMA_VERSION = 1
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FORBIDDEN_KEYS = {
    "ki", "k_i", "opc", "op_c", "op", "k", "milenage", "aka_key", "subscriber_key",
}

IMS_AF = {"auto", "v4", "v6", "dual"}
IDR_MODES = {"apn", "fqdn"}
BSSID_POLICIES = {"derived", "placeholder"}
PROBE_STEPS = {"v6", "v4", "dual"}

_lock = threading.RLock()
_cache: list[dict] | None = None


class ProfileError(ValueError):
    """Raised when a profile document is not acceptable."""


def user_profile_dir() -> str:
    from . import config as cfg
    return os.path.join(cfg.DATA_DIR, "carrier-profiles")


def plmn_keys(mcc, mnc) -> tuple[str, ...]:
    mcc_s, mnc_s = str(mcc or "").strip(), str(mnc or "").strip()
    if not mcc_s or not mnc_s:
        return ()
    return (
        f"{mcc_s}-{mnc_s}",
        f"{mcc_s.zfill(3)}-{mnc_s.zfill(3)}",
        f"{mcc_s}-{mnc_s.lstrip('0') or mnc_s}",
    )


def _reject_secrets(raw: Any, path: str = "profile"):
    if isinstance(raw, dict):
        for key, value in raw.items():
            name = str(key).strip().lower().replace("-", "_")
            if name in FORBIDDEN_KEYS:
                raise ProfileError(
                    f"{path}: {key} is AKA/subscriber material and is not accepted")
            _reject_secrets(value, f"{path}.{key}")
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            _reject_secrets(value, f"{path}[{index}]")


def _norm_mcc_mnc(value, *, kind: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if kind == "mcc" and len(digits) == 3:
        return digits
    if kind == "mnc" and 2 <= len(digits) <= 3:
        return digits
    raise ProfileError(f"invalid {kind}: {value!r}")


def _norm_probe_order(value) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        items = [part.strip().lower() for part in value.split(",") if part.strip()]
    elif isinstance(value, list):
        items = [str(part).strip().lower() for part in value if str(part).strip()]
    else:
        raise ProfileError("probe_order must be a list or comma-separated string")
    for item in items:
        if item not in PROBE_STEPS:
            raise ProfileError(f"unknown probe_order step: {item}")
    # Keep first occurrence; the engine also dedupes.
    out = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def normalize_profile(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ProfileError("profile must be an object")
    _reject_secrets(raw)
    version = int(raw.get("version") or SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ProfileError(f"unsupported profile version {version}")
    ident = str(raw.get("id") or "").strip().lower()
    if not PROFILE_ID_RE.match(ident):
        raise ProfileError("id must be a short lowercase token")
    matches = []
    for row in raw.get("matches") or []:
        if not isinstance(row, dict):
            raise ProfileError("matches entries must be objects")
        matches.append({
            "mcc": _norm_mcc_mnc(row.get("mcc"), kind="mcc"),
            "mnc": _norm_mcc_mnc(row.get("mnc"), kind="mnc"),
        })
    if not matches:
        raise ProfileError("at least one MCC-MNC match is required")
    overrides = raw.get("overrides") if isinstance(raw.get("overrides"), dict) else {}
    extra = {key: raw[key] for key in (
        "epdg", "realm", "ims_af", "probe_order", "pani_country", "pani_bssid_policy",
        "access_type", "user_eq_phone", "smsc", "apn", "idr_mode",
    ) if key in raw and key not in overrides}
    overrides = {**extra, **overrides}

    ims_af = str(overrides.get("ims_af") or "").strip().lower()
    if ims_af and ims_af not in IMS_AF:
        raise ProfileError(f"ims_af must be one of {sorted(IMS_AF)}")
    idr_mode = str(overrides.get("idr_mode") or "").strip().lower()
    if idr_mode and idr_mode not in IDR_MODES:
        raise ProfileError(f"idr_mode must be one of {sorted(IDR_MODES)}")
    bssid = str(overrides.get("pani_bssid_policy") or "").strip().lower()
    if bssid and bssid not in BSSID_POLICIES:
        raise ProfileError(f"pani_bssid_policy must be one of {sorted(BSSID_POLICIES)}")
    pani_country = str(overrides.get("pani_country") or "").strip().upper()
    if pani_country and not re.fullmatch(r"[A-Z]{2}", pani_country):
        raise ProfileError("pani_country must be an ISO 3166-1 alpha-2 code")
    smsc = str(overrides.get("smsc") or "").strip()
    if smsc and not re.fullmatch(r"\+?[0-9]{6,15}", smsc):
        raise ProfileError("smsc must be an E.164-like number")

    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return {
        "version": SCHEMA_VERSION,
        "id": ident,
        "name": str(raw.get("name") or ident),
        "matches": matches,
        "epdg": str(overrides.get("epdg") or "").strip(),
        "realm": str(overrides.get("realm") or "").strip(),
        "ims_af": ims_af,
        "probe_order": _norm_probe_order(overrides.get("probe_order")),
        "pani_country": pani_country,
        "pani_bssid_policy": bssid or "derived",
        "access_type": str(overrides.get("access_type") or "").strip(),
        "user_eq_phone": (None if "user_eq_phone" not in overrides
                          else bool(overrides.get("user_eq_phone"))),
        "smsc": smsc,
        "apn": str(overrides.get("apn") or "").strip().lower(),
        "idr_mode": idr_mode,
        "source": {
            "kind": str(source.get("kind") or "manual").strip().lower() or "manual",
            "note": str(source.get("note") or "")[:200],
        },
    }


def load_document(text: str, *, filename: str = "profile") -> dict:
    raw = None
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        raw = json.loads(text)
    else:
        raw = yaml.safe_load(text)
    if isinstance(raw, list):
        raise ProfileError(f"{filename}: one profile per file")
    try:
        return normalize_profile(raw or {})
    except ProfileError as exc:
        raise ProfileError(f"{filename}: {exc}") from exc


def dump_document(profile: dict) -> str:
    doc = {
        "version": SCHEMA_VERSION,
        "id": profile["id"],
        "name": profile.get("name") or profile["id"],
        "matches": profile.get("matches") or [],
        "overrides": {key: profile[key] for key in (
            "epdg", "realm", "ims_af", "probe_order", "pani_country",
            "pani_bssid_policy", "access_type", "user_eq_phone", "smsc", "apn",
            "idr_mode",
        ) if profile.get(key) not in (None, "", [])},
        "source": profile.get("source") or {"kind": "manual", "note": ""},
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def _read_dir(path: str) -> list[dict]:
    if not os.path.isdir(path):
        return []
    found = []
    for name in sorted(os.listdir(path)):
        if not name.endswith((".yaml", ".yml", ".json")):
            continue
        full = os.path.join(path, name)
        try:
            with open(full, encoding="utf-8") as handle:
                found.append(load_document(handle.read(), filename=name))
        except Exception as exc:
            log.warning("skipping carrier profile %s: %s", full, exc)
    return found


def reload() -> list[dict]:
    global _cache
    with _lock:
        _cache = _read_dir(user_profile_dir())
        return list(_cache)


def loaded() -> list[dict]:
    with _lock:
        if _cache is None:
            return reload()
        return list(_cache)


def reset_cache():
    global _cache
    with _lock:
        _cache = None


def save_profile(profile: dict) -> str:
    normalized = normalize_profile(profile)
    directory = user_profile_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, f"{normalized['id']}.yaml")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(dump_document(normalized))
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    reset_cache()
    return path


def match(mcc, mnc, profile_id: str | None = None) -> dict | None:
    wanted = str(profile_id or "").strip().lower()
    keys = set(plmn_keys(mcc, mnc))
    for profile in loaded():
        if wanted and profile["id"] != wanted:
            continue
        if wanted and profile["id"] == wanted:
            return deepcopy(profile)
        for row in profile.get("matches") or []:
            if keys & set(plmn_keys(row.get("mcc"), row.get("mnc"))):
                return deepcopy(profile)
    return None


def sip_hint(mcc, mnc, profile_id: str | None = None) -> dict:
    """PANI / access_type / user_eq_phone fields from a loaded file profile."""
    profile = match(mcc, mnc, profile_id)
    if not profile:
        return {}
    out = {}
    if profile.get("pani_country"):
        out["pani_country"] = profile["pani_country"]
        out["pani_bssid_policy"] = profile.get("pani_bssid_policy") or "derived"
    if profile.get("access_type"):
        out["access_type"] = profile["access_type"]
    if profile.get("user_eq_phone") is not None:
        out["user_eq_phone"] = bool(profile["user_eq_phone"])
    return out


def probe_order(mcc, mnc, profile_id: str | None = None) -> list[str]:
    profile = match(mcc, mnc, profile_id)
    if not profile:
        return []
    if profile.get("probe_order"):
        return list(profile["probe_order"])
    af = profile.get("ims_af")
    if af in ("v4", "v6", "dual"):
        return [af]
    return []


def apply_to_instance(inst: dict) -> dict:
    """Resolved overlay for one line. Explicit instance fields always win."""
    profile = match(inst.get("mcc"), inst.get("mnc"), inst.get("carrier_profile"))
    overlay = {
        "carrier_profile_id": (profile or {}).get("id") or "",
        "epdg": str(inst.get("epdg") or (profile or {}).get("epdg") or ""),
        "realm": str(inst.get("realm") or (profile or {}).get("realm") or ""),
        "smsc": str(inst.get("smsc") or (profile or {}).get("smsc") or ""),
        "apn": str(inst.get("apn") or (profile or {}).get("apn") or ""),
        "idr_mode": str(inst.get("idr_mode") or (profile or {}).get("idr_mode") or ""),
        "ims_af": str((profile or {}).get("ims_af") or ""),
        "probe_order": list((profile or {}).get("probe_order") or []),
    }
    return overlay
