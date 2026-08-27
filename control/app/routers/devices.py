"""HTTP routes: physical devices and capability toggles."""

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

@router.get("/api/devices")
@_w
async def api_devices():
    # Sessions are memory-only, so a sign-in usually follows a control-plane restart — right
    # when the card monitor is still completing its first scan and smart-card readers are not
    # in the list yet. `discovering` lets the UI say so instead of reporting a confident zero.
    return {"devices": await _unified_devices(), "discovering": not hub.scanned,
            "shared": device_state.status().get("shared") or {}}


@router.put("/api/devices/{device_id}/hardware")
@_w
async def api_device_hardware(device_id: str, body: dict):
    """Save user-managed physical hardware identity (currently native-reader IMEI)."""
    if set(body or {}) - {"imei"}:
        raise HTTPException(400, "only imei can be changed")
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    if not device:
        raise HTTPException(404, "no such physical device")
    if device.get("device_type") != "reader":
        raise HTTPException(400, "a modem reports its hardware IMEI automatically")
    raw = str((body or {}).get("imei") or "").strip()
    imei = cfg.normalize_imei(raw)
    if len(imei) != 15:
        raise HTTPException(422, "IMEI must contain exactly 15 digits")
    record = device_state.set_hardware(device_id, {
        "device_type": "reader", "name": device.get("name") or "Smart-card reader",
        "stable_path": device.get("stable_path") or "", "imei": imei})

    # A running line renders the device identity inside its container. Apply a hardware
    # change immediately to the SIM currently inserted in this reader.
    iid = str(device.get("instance_id") or "")
    applied = False
    if iid and imei:
        inst = cfg.get_instance(iid) or {}
        previous_imeisv = str(inst.get("imeisv") or "")
        svn = (previous_imeisv[-2:] if len(previous_imeisv) == 16
               and previous_imeisv[-2:].isdigit() else _random_svn())
        inst = cfg.upsert_instance({"id": iid, "imei": imei,
                                    "imei_source_device_id": device_id,
                                    "imeisv": cfg.imeisv_from_imei(imei, svn=svn)})
        if await asyncio.to_thread(engine.is_running, iid):
            await hub.drop_ami(iid)
            await asyncio.to_thread(_start_engine_checked, inst, cfg.get_settings(),
                                    dev_mounts=os.environ.get("MDD_DEV_MOUNTS", "") == "1")
            hub.reset_health(iid)
            applied = True
    await hub.broadcast({"type": "hardware", "device": device_id})
    return {"ok": True, "imei_masked": _masked_identifier(record.get("imei")),
            "applied": applied}


@_w
def _remove_device_from_document(path: str, device_id: str, mapping_key: str) -> None:
    document = _read_json_file(path)
    mapping = document.get(mapping_key)
    if not isinstance(mapping, dict) or device_id not in mapping:
        return
    mapping.pop(device_id, None)
    document["updated_at"] = int(time.time())
    temporary = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


@router.delete("/api/devices/{device_id}")
@_w
async def api_device_delete(device_id: str):
    """Forget an offline physical device without deleting any SIM/line configuration."""
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    if not device:
        raise HTTPException(404, "no such physical device")
    if device.get("present"):
        raise HTTPException(409, "disconnect the physical device before forgetting it")
    device_state.remove_desired(device_id)
    device_state.remove_hardware(device_id)
    orchestrator_root = os.path.join(cfg.DATA_DIR, "orchestrator")
    _remove_device_from_document(os.path.join(orchestrator_root, "hardware-state.json"),
                                 device_id, "assignments")
    _remove_device_from_document(os.path.join(orchestrator_root, "devices-status.json"),
                                 device_id, "devices")
    for path in glob.glob(os.path.join(cfg.DATA_DIR, "modems", "*.json")):
        identity = _read_json_file(path)
        if str(identity.get("hardware_id") or "") == device_id:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
    await hub.broadcast({"type": "hardware", "device": device_id, "event": "forgotten"})
    return {"ok": True, "device_id": device_id, "lines_preserved": True}


@router.get("/api/devices/{device_id}/cellular")
@_w
async def api_device_cellular(device_id: str):
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    if not device:
        raise HTTPException(404, "no such physical device")
    return {"device_id": device_id, "capability": device["capabilities"]["cellular"],
            "cellular": device.get("cellular")}

@router.post("/api/devices/{device_id}/diagnostics")
@_w
async def api_device_diagnostics(device_id: str):
    device = next((item for item in await _unified_devices() if item["id"] == device_id), None)
    if not device:
        raise HTTPException(404, "no such physical device")
    checks = [
        {"name": "hardware", "ok": bool(device.get("present")),
         "detail": "detected" if device.get("present") else "not detected"},
        {"name": "cellular", "ok": device["capabilities"]["cellular"]["actual"] in {"on", "off", "unsupported"},
         "detail": device["capabilities"]["cellular"]["actual"]},
        {"name": "vowifi", "ok": device["capabilities"]["vowifi"]["actual"] in {"on", "off"},
         "detail": device["capabilities"]["vowifi"]["actual"]},
        {"name": "country_egress", "ok": (not device["capabilities"]["vowifi"]["desired"]
                                             or bool(device.get("egress", {}).get("node"))),
         "detail": device.get("egress", {}).get("node") or "not selected"},
    ]
    return {"ok": all(item["ok"] for item in checks), "device_id": device_id,
            "checked_at": int(time.time()), "checks": checks}


@_w
async def _wait_for_device_request(device_id: str, wanted: dict, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    latest = {}
    while time.monotonic() < deadline:
        latest = device_state.status()
        current = (latest.get("devices") or {}).get(device_id) or {}
        observed_wanted = current.get("desired") or {}
        if (all(observed_wanted.get(key) == value for key, value in wanted.items())
                and not current.get("transitioning")
                and not (latest.get("shared") or {}).get("transitioning")):
            # A shared MM shutdown resets the USB modem. The orchestrator can publish one
            # intermediate "not connected" sample after the desired state is applied; wait for
            # re-enumeration instead of reporting a failed toggle that actually succeeded.
            if not current.get("present", True) or current.get("error") == "device is not connected":
                await asyncio.sleep(.5)
                continue
            if current.get("error") or (latest.get("shared") or {}).get("error"):
                raise RuntimeError(current.get("error") or latest["shared"]["error"])
            return latest
        await asyncio.sleep(.5)
    raise TimeoutError("device capability transition timed out")


@_w
async def _resume_instances(instance_ids: set[str], skip: set[str] | None = None) -> dict:
    failed = {}
    for iid in sorted(instance_ids - (skip or set())):
        inst = cfg.get_instance(iid)
        if not inst:
            continue
        try:
            # Use the full manual-start path so a retry clears frozen health, refreshes the
            # current reader binding, checks PIN/card identity and drops a stale AMI client.
            await api_instance_start(iid)
        except Exception as exc:
            failed[iid] = str(getattr(exc, "detail", exc))
    return failed


@router.patch("/api/devices/{device_id}/capabilities")
@_w
async def api_device_capabilities(device_id: str, body: dict):
    allowed = {"cellular_enabled", "vowifi_enabled", "flight_mode"}
    if not body or not set(body).issubset(allowed):
        raise HTTPException(400, "provide cellular_enabled, vowifi_enabled and/or flight_mode only")
    if any(not isinstance(value, bool) for value in body.values()):
        raise HTTPException(400, "capability values must be boolean")

    async with capability_lock:
        unified = await _unified_devices()
        device = next((item for item in unified if item["id"] == device_id), None)
        if not device:
            raise HTTPException(404, "no such physical device")
        if device.get("device_type") == "reader":
            if "cellular_enabled" in body or "flight_mode" in body:
                raise HTTPException(400, "a smart-card reader has no cellular radio")
            iid = str(device.get("instance_id") or "")
            if not iid:
                if body.get("vowifi_enabled"):
                    raise HTTPException(409, "configure the SIM before enabling VoWiFi")
                return device
            inst = cfg.get_instance(iid)
            previous = bool((inst or {}).get("enabled", True))
            wanted = bool(body.get("vowifi_enabled", previous))
            retry = bool(wanted and not await asyncio.to_thread(engine.is_running, iid))
            if wanted == previous and not retry:
                return device
            if wanted:
                cfg.upsert_instance({"id": iid, "enabled": True})
                await api_instance_start(iid)
            else:
                cfg.upsert_instance({"id": iid, "enabled": False})
                await api_instance_stop(iid)
            refreshed = await _unified_devices()
            return next(item for item in refreshed if item["id"] == device_id)

        desired_doc, observed_doc, assignments = _device_sources()
        known = set(assignments) | set(desired_doc.get("devices") or {}) | set(observed_doc.get("devices") or {})
        if device_id not in known:
            raise HTTPException(404, "no such physical device")
        present = sorted(key for key in known if (observed_doc.get("devices") or {}).get(
            key, {}).get("present", key in assignments))
        previous = (desired_doc.get("devices") or {}).get(device_id) or desired_doc.get("defaults") or {
            "cellular_enabled": False, "vowifi_enabled": True, "flight_mode": False}
        wanted = {**previous, **body}
        cellular_changed = wanted["cellular_enabled"] != bool(previous.get("cellular_enabled"))
        vowifi_changed = wanted["vowifi_enabled"] != bool(previous.get("vowifi_enabled"))
        flight_changed = bool(wanted.get("flight_mode")) != bool(previous.get("flight_mode"))

        identities = _device_identities()
        cards = hub.cards_list()
        target_observed = (observed_doc.get("devices") or {}).get(device_id) or {}
        target_instance = _instance_for_device(
            device_id, identities.get(device_id) or {}, cards, target_observed)
        if (target_instance and target_instance.get("provisioning_state") == "draft"
                and body.get("vowifi_enabled") is True):
            card_info = next((item for item in cards
                              if item.get("present")
                              and (identity.iccids_equal(item.get("iccid"),
                                                         target_instance.get("iccid"))
                                   or str(item.get("matched") or "")
                                   == str(target_instance["id"]))), None)
            if card_info:
                target_instance = await asyncio.to_thread(
                    _auto_promote_card_draft, target_instance, card_info, cards,
                    enable=True)
        target_iid = str(target_instance["id"]) if target_instance else ""
        # Repeating an ON request is an explicit retry when the device-level intent says ON
        # but the line is disabled or its engine has stopped. Do not discard it as a no-op.
        vowifi_retry = bool(
            body.get("vowifi_enabled") is True and target_instance
            and (not target_instance.get("enabled", True)
                 or not await asyncio.to_thread(engine.is_running, target_iid)))
        if not cellular_changed and not vowifi_changed and not flight_changed and not vowifi_retry:
            devices = await _unified_devices()
            return next(item for item in devices if item["id"] == device_id)
        vowifi_action = vowifi_changed or vowifi_retry
        # Data bearer and flight-mode changes are reconciled underneath the existing line.
        # Only a VoWiFi toggle intentionally stops/starts that line.
        affected_instances = [target_instance] if vowifi_action and target_instance else []
        running_ids = []
        for inst in affected_instances:
            if inst and await asyncio.to_thread(engine.is_running, str(inst["id"])):
                running_ids.append(str(inst["id"]))
                await asyncio.to_thread(engine.stop, str(inst["id"]))
                await hub.drop_ami(str(inst["id"]))
                if not wanted["vowifi_enabled"]:
                    # Record the stop the way an explicit line stop does. Otherwise the last
                    # observation of the running line — NO_CARD, REGISTERING, whatever it was
                    # — stays authoritative until the next poll and the UI reports a problem
                    # with a line the operator just switched off.
                    hub.status_cache[str(inst["id"])] = _with_status_activity(
                        str(inst["id"]),
                        {"state": "STOPPED", "label": status_mod.LABELS["STOPPED"],
                         "reason_code": "stopped", "reason": "Stopped.", "detail": {}})
                    hub.status_sampled_at[str(inst["id"])] = time.monotonic()

        device_state.set_desired(device_id,
                                 cellular_enabled=wanted["cellular_enabled"],
                                 vowifi_enabled=wanted["vowifi_enabled"],
                                 flight_mode=bool(wanted.get("flight_mode")))
        if target_iid and vowifi_action:
            target_instance = cfg.upsert_instance({
                "id": target_iid, "enabled": bool(wanted["vowifi_enabled"])})
        egress.publish()
        skip_resume = {target_iid} if target_iid and not wanted["vowifi_enabled"] else set()
        try:
            await _wait_for_device_request(device_id, wanted)
        except TimeoutError as exc:
            await _resume_instances(set(running_ids), skip_resume)
            raise HTTPException(504, str(exc)) from exc
        except RuntimeError as exc:
            await _resume_instances(set(running_ids), skip_resume)
            raise HTTPException(503, str(exc)) from exc

        resume_ids = set(running_ids)
        if vowifi_action and wanted["vowifi_enabled"] and target_instance:
            resume_ids.add(str(target_instance["id"]))
        failed = await _resume_instances(resume_ids, skip_resume)
        await hub.broadcast({"type": "capability", "device": device_id, "desired": wanted,
                             "resume_failed": failed})
        devices = await _unified_devices()
        response = next(item for item in devices if item["id"] == device_id)
        if failed:
            response["resume_failed"] = failed
        return response

EXPORTS = ('api_devices', 'api_device_hardware', '_remove_device_from_document', 'api_device_delete', 'api_device_cellular', 'api_device_diagnostics', '_wait_for_device_request', '_resume_instances', 'api_device_capabilities')
