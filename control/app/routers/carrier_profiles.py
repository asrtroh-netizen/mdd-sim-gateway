"""HTTP routes: owned carrier interoperability profiles."""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import time

from fastapi import APIRouter, HTTPException

from control.app.ami import AmiClient
from control.app.routers._bind import with_main

router = APIRouter()

_MAIN_NAMES = (
    "hub", "cfg", "engine", "store", "log", "identity", "device_state",
    "egress", "sim", "lpa", "estkme", "esim_lifecycle", "modem_engineering",
    "carrier_profile", "carrier_ipcc", "status_mod", "sms_pdu", "allowance",
    "capability_lock", "cellular_sms",
    "LINE_HISTORY_MIN_SECONDS", "LINE_HISTORY_MAX_SECONDS",
    "_unified_devices", "_modem_device_or_400", "_modem_path_or_409",
    "_remove_device_from_document", "_read_json_file", "_masked_identifier",
    "_random_svn", "_start_engine_checked", "_device_sources", "_device_identities",
    "_instance_for_device", "_auto_promote_card_draft", "_wait_for_device_request",
    "_resume_instances", "_with_status_activity", "_cached_line_status",
    "_reader_index_for_instance", "_reader_port_for_instance",
    "_card_identity_mismatch", "_raise_card_mismatch", "_preflight_pin",
    "_refresh_card_matches", "_client_cards", "_auto_start_hotplugged_line",
    "_find_running_by_reader",
    "_esim_resolve_reader", "_esim_imei_for_reader", "_esim_resolve_se",
    "_esim_guard_engine", "_esim_refresh_card", "_esim_switch_identity",
    "_esim_modem_reader_names", "_esim_prepare_profile_switch",
    "_esim_prepare_reader_switch", "_esim_stop_for_lpa", "_esim_bind_after_switch",
    "_esim_restore_profile_switch", "_esim_recover_profile_switch", "_esim_run",
    "_esim_cache_store", "_esim_cache_for_iccid", "_esim_cache_update_profile",
    "_line_state_written", "_line_registered_written",
    "api_instance_start", "api_instance_stop", "api_instance_upsert",
    "send_sms_on_line", "detect_sms_result", "push_status",
)


def bind(main_module):
    from control.app.routers._bind import apply
    apply(globals(), main_module, _MAIN_NAMES)


def _w(fn):
    return with_main(*_MAIN_NAMES)(fn)

# ----------------------------- carrier profiles -----------------------------
@router.get("/api/carrier-profiles")
@_w
async def api_carrier_profiles():
    """Loaded YAML/JSON interoperability profiles (not a VoCat/IPCC dump)."""
    return {"ok": True, "profiles": carrier_profile.loaded(),
            "dir": carrier_profile.user_profile_dir()}


@router.post("/api/carrier-profiles/import-ipcc")
@_w
async def api_carrier_profiles_import_ipcc(body: dict | None = None):
    """Map a user-supplied IPCC zip/plist on this host into the owned profile schema."""
    body = body or {}
    path = str(body.get("path") or "").strip()
    if not path:
        raise HTTPException(400, "path to a local .ipcc / .zip / .plist is required")
    try:
        profile = await asyncio.to_thread(
            carrier_ipcc.import_ipcc, path,
            profile_id=body.get("id") or None,
            name=str(body.get("name") or ""),
            persist=True)
    except carrier_profile.ProfileError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "profile": profile}

EXPORTS = ('api_carrier_profiles', 'api_carrier_profiles_import_ipcc')
