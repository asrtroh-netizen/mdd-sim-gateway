"""HTTP routes: modem engineering (AT, USSD, operators, usbnet, restart)."""

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

@_w
def _modem_device_or_400(device: dict | None) -> dict:
    if not device:
        raise HTTPException(404, "no such physical device")
    if device.get("device_type") == "reader":
        raise HTTPException(400, "a smart-card reader has no cellular radio")
    return device


@_w
def _modem_path_or_409(device_id: str) -> str:
    path = modem_engineering.modem_path_for_device(device_id)
    if not path:
        raise HTTPException(409, "the cellular modem is not visible to ModemManager")
    return path

@router.get("/api/devices/{device_id}/at")
@_w
async def api_device_at_history(device_id: str):
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    _modem_device_or_400(device)
    return {"device_id": device_id, "history": modem_engineering.history_for(device_id)}


@router.post("/api/devices/{device_id}/at")
@_w
async def api_device_at(device_id: str, body: dict):
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    _modem_device_or_400(device)
    path = _modem_path_or_409(device_id)
    command = str((body or {}).get("command") or "")
    result = await asyncio.to_thread(modem_engineering.send_at, path, command)
    if result.get("stage") == "validate":
        raise HTTPException(400, result.get("error") or "invalid AT command")
    modem_engineering.record_history(
        device_id, result.get("command") or "",
        result.get("response") or result.get("error") or "",
        ok=bool(result.get("ok")))
    result["history"] = modem_engineering.history_for(device_id)
    return result


@router.post("/api/devices/{device_id}/ussd")
@_w
async def api_device_ussd(device_id: str, body: dict):
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    _modem_device_or_400(device)
    path = _modem_path_or_409(device_id)
    code = str((body or {}).get("code") or (body or {}).get("ussd") or "")
    result = await asyncio.to_thread(modem_engineering.send_ussd, path, code)
    if result.get("stage") == "validate":
        raise HTTPException(400, result.get("error") or "invalid USSD code")
    return result


@router.post("/api/devices/{device_id}/operators/scan")
@_w
async def api_device_operator_scan(device_id: str):
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    _modem_device_or_400(device)
    path = _modem_path_or_409(device_id)
    return await asyncio.to_thread(modem_engineering.scan_operators, path)


@router.post("/api/devices/{device_id}/operators/select")
@_w
async def api_device_operator_select(device_id: str, body: dict):
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    _modem_device_or_400(device)
    path = _modem_path_or_409(device_id)
    result = await asyncio.to_thread(
        modem_engineering.select_operator, path,
        mode=str((body or {}).get("mode") or ""),
        plmn=str((body or {}).get("plmn") or (body or {}).get("operator") or ""))
    if result.get("stage") == "validate":
        raise HTTPException(400, result.get("error") or "invalid operator selection")
    return result


@router.get("/api/devices/{device_id}/usbnet")
@_w
async def api_device_usbnet(device_id: str):
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    _modem_device_or_400(device)
    path = _modem_path_or_409(device_id)
    return await asyncio.to_thread(modem_engineering.usbnet_status, path)


@router.put("/api/devices/{device_id}/usbnet")
@_w
async def api_device_usbnet_set(device_id: str, body: dict):
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    _modem_device_or_400(device)
    path = _modem_path_or_409(device_id)
    mode = (body or {}).get("mode", (body or {}).get("usbnet"))
    result = await asyncio.to_thread(modem_engineering.set_usbnet, path, mode)
    if result.get("stage") == "validate":
        raise HTTPException(400, result.get("error") or "invalid USB net mode")
    return result


@router.post("/api/devices/{device_id}/restart")
@_w
async def api_device_restart(device_id: str):
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    _modem_device_or_400(device)
    path = _modem_path_or_409(device_id)
    return await asyncio.to_thread(modem_engineering.restart_modem, path)

EXPORTS = ('_modem_device_or_400', '_modem_path_or_409', 'api_device_at_history', 'api_device_at', 'api_device_ussd', 'api_device_operator_scan', 'api_device_operator_select', 'api_device_usbnet', 'api_device_usbnet_set', 'api_device_restart')
