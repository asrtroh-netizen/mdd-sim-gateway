"""eSIM profile-switch helpers that do not speak to lpac or the engine.

Used by the control plane after an LPA enable/switch so the logical line follows the
new ICCID (case-insensitive) and the UI can recover from a leftover foreign card.
Certificate fields are copied from lpac's chip-info payload when present — this is
not a second LPA implementation.
"""
from __future__ import annotations

from . import identity


def chip_certificates(raw: dict | None) -> dict:
    """Surface CI PKI identifiers lpac already returns inside EUICCInfo2."""
    info2 = (raw or {}).get("EUICCInfo2") or {}
    verify = info2.get("euiccCiPKIdListForVerification") or []
    sign = info2.get("euiccCiPKIdListForSigning") or []
    if not isinstance(verify, list):
        verify = []
    if not isinstance(sign, list):
        sign = []
    sas = (info2.get("sasAccreditationNumber")
           or info2.get("sasAcreditationNumber") or "")
    return {
        "ci_verify": [str(item) for item in verify if item],
        "ci_sign": [str(item) for item in sign if item],
        "sas": str(sas or ""),
    }


def profile_iccid_matches(left, right) -> bool:
    return identity.iccids_equal(left, right)


def cache_entry_for_iccid(cache: dict | None, iccid: str) -> dict | None:
    """Find a persisted chip cache whose profiles include this ICCID."""
    needle = identity.normalize_iccid(iccid)
    if not needle or not isinstance(cache, dict):
        return None
    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        for se in entry.get("ses") or []:
            for profile in se.get("profiles") or []:
                if profile_iccid_matches(profile.get("iccid"), needle):
                    return entry
    return None


def update_cached_profile(cache: dict, iccid: str, *, state: str | None = None,
                          nickname: str | None = None, remove: bool = False) -> bool:
    """Mirror enable/disable/delete/nickname onto a chip cache. Returns True if written."""
    if not isinstance(cache, dict) or not iccid:
        return False
    changed = False
    for entry in cache.values():
        if not isinstance(entry, dict):
            continue
        for se in entry.get("ses") or []:
            profiles = list(se.get("profiles") or [])
            hit = next((row for row in profiles
                        if profile_iccid_matches(row.get("iccid"), iccid)), None)
            if hit is None:
                continue
            if remove:
                se["profiles"] = [row for row in profiles
                                  if not profile_iccid_matches(row.get("iccid"), iccid)]
            else:
                if state == "enabled":
                    for row in profiles:
                        if str(row.get("profileState") or "").lower() == "enabled":
                            row["profileState"] = "disabled"
                if state is not None:
                    hit["profileState"] = state
                if nickname is not None:
                    hit["profileNickname"] = nickname
            changed = True
    return changed


def reader_busy_detail(instance_id, *, reader: str = "") -> dict:
    iid = str(instance_id or "")
    where = f" on {reader}" if reader else ""
    return {
        "code": "reader_busy",
        "instance_id": iid,
        "reader": reader,
        "message": (
            f"Line {iid} is running{where}. eSIM Load/download/switch needs exclusive "
            "PC/SC access; stop VoWiFi on this reader first."
        ),
    }


def leftover_card_detail(reader: str, live_iccid: str, expected_iccid: str) -> dict:
    return {
        "code": "card_mismatch",
        "reader": reader,
        "card_iccid": live_iccid,
        "line_iccid": expected_iccid,
        "message": (
            f"The card in {reader} still reports {live_iccid or 'unknown'}, not the "
            f"enabled profile {expected_iccid}. The previous profile was not started. "
            "Load the eSIM page and switch again, or start the line that matches the "
            "card now in the reader."
        ),
    }
