"""HTTP routes: eSIM / LPA."""

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

# ----------------------------- eSIM / LPA (lpac) -----------------------------
@router.get("/api/esim/status")
@_w
async def api_esim_status():
    """Whether lpac is installed and basic settings."""
    settings = cfg.get_settings().get("esim") or {}
    bin_path = lpa.lpac_bin()
    return {
        "available": lpa.lpac_available(),
        "lpac_bin": bin_path,
        "download_timeout": int(settings.get("download_timeout") or 300),
        "auto_process_notifications": bool(settings.get("auto_process_notifications", True)),
        "busy_readers": list(hub.lpa_busy.keys()),
    }


# ---------------------------------------------------------------- eSIM chip cache
# Last successful chip read per eUICC (keyed by EID), persisted in the data dir so every
# browser/session can show the profile list — and switch profiles — without stopping a
# running line for a fresh exclusive read. Entries are matched to the inserted card via the
# ICCIDs of their profiles (the card monitor reads the active ICCID without exclusivity).
def _esim_cache_path() -> str:
    return os.path.join(cfg.DATA_DIR, "esim-chip-cache.json")




@_w
def _esim_cache_load() -> dict:
    doc = _read_json_file(_esim_cache_path())
    return doc if isinstance(doc, dict) else {}


@_w
def _esim_cache_write(data: dict):
    tmp = _esim_cache_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, _esim_cache_path())


@_w
def _esim_cache_store(ses: list, imei: str):
    eid = next((str(se.get("eid")) for se in ses if se.get("eid")), "")
    # Only a fully successful read may overwrite the cache — a partial/failed load would
    # replace a good profile list with an empty one.
    if not eid or any(se.get("error") for se in ses):
        return
    data = _esim_cache_load()
    data[eid] = {"ses": ses, "imei": imei or "", "ts": int(time.time())}
    _esim_cache_write(data)


@_w
def _esim_cache_for_iccid(iccid: str) -> dict | None:
    return esim_lifecycle.cache_entry_for_iccid(_esim_cache_load(), iccid)


@_w
def _esim_cache_update_profile(iccid: str, *, state: str | None = None,
                               nickname: str | None = None, remove: bool = False):
    """Mirror a successful enable/disable/delete/nickname onto the cached view."""
    data = _esim_cache_load()
    if esim_lifecycle.update_cached_profile(
            data, iccid, state=state, nickname=nickname, remove=remove):
        _esim_cache_write(data)


@router.get("/api/esim/chip/cached")
@_w
async def api_esim_chip_cached(reader_index: int = 0, reader: str | None = None):
    """Cached chip view for the card in this reader — never touches the card, so it is safe
    while a VoWiFi line holds the reader."""
    name, idx = await asyncio.to_thread(_esim_resolve_reader, reader_index, reader)
    iccid = str((hub.cards.get(name) or {}).get("iccid") or "")
    entry = await asyncio.to_thread(_esim_cache_for_iccid, iccid)
    if not entry:
        return {"ok": True, "cached": False, "reader": name, "reader_index": idx}
    return {"ok": True, "cached": True, "reader": name, "reader_index": idx,
            "ses": entry.get("ses") or [], "imei": entry.get("imei") or "",
            "ts": entry.get("ts") or 0}


@router.get("/api/esim/chip")
@_w
async def api_esim_chip(reader_index: int = 0, reader: str | None = None,
                       stop: bool = False):
    """Load chip info for every SE on the card (dual SE → two entries)."""
    name, idx = await asyncio.to_thread(_esim_resolve_reader, reader_index, reader)
    if stop:
        await _esim_stop_for_lpa(name)
    running = await asyncio.to_thread(_find_running_by_reader, name)
    payload = await _esim_run(name, idx, lpa.load_all_ses(name, idx))
    ses = payload.get("ses") or []
    await asyncio.to_thread(_esim_cache_store, ses, _esim_imei_for_reader(name))
    # Backward-compatible single-chip view = first SE that loaded successfully.
    primary = next((s for s in ses if s.get("chip")), ses[0] if ses else None)
    return {
        "ok": True,
        "reader": name,
        "reader_index": idx,
        "dual": bool(payload.get("dual")),
        "ses": ses,
        "chip": (primary or {}).get("chip"),
        "imei": _esim_imei_for_reader(name),
        "line_running": bool(running),
        "matched_instance": running["id"] if running else (hub.cards.get(name) or {}).get("matched"),
    }


@router.get("/api/esim/profiles")
@_w
async def api_esim_profiles(reader_index: int = 0, reader: str | None = None):
    """List profiles grouped per SE (same load as chip — prefer /api/esim/chip for full view)."""
    name, idx = await asyncio.to_thread(_esim_resolve_reader, reader_index, reader)
    running = await asyncio.to_thread(_find_running_by_reader, name)
    payload = await _esim_run(name, idx, lpa.load_all_ses(name, idx))
    ses = payload.get("ses") or []
    flat = []
    for se in ses:
        flat.extend(se.get("profiles") or [])
    return {
        "ok": True,
        "reader": name,
        "reader_index": idx,
        "dual": bool(payload.get("dual")),
        "ses": ses,
        "profiles": flat,
        "imei": _esim_imei_for_reader(name),
        "line_running": bool(running),
        "matched_instance": running["id"] if running else (hub.cards.get(name) or {}).get("matched"),
        "lpa_busy": bool(hub.lpa_busy.get(name)),
    }


@router.post("/api/esim/profiles/{iccid}/enable")
@_w
async def api_esim_enable(iccid: str, body: dict | None = None):
    body = body or {}
    name, idx = await asyncio.to_thread(
        _esim_resolve_reader, body.get("reader_index", 0), body.get("reader"))
    switch_key, hardware_id = _esim_switch_identity(name)
    async with hub.esim_switch_lock(switch_key):
        # Native readers have no host bridge to recycle. Stop VoWiFi, enable the
        # profile, then rebind the same logical line to the new ICCID.
        if not hardware_id:
            previous = await _esim_prepare_reader_switch(name)
            lpa_succeeded = False
            try:
                se = await asyncio.to_thread(
                    _esim_resolve_se, name, idx, body.get("se_id") or body.get("seId"),
                    body.get("aid"), require=True)
                await _esim_run(
                    name, idx, lpa.profile_enable(name, iccid, aid=se.get("aid")),
                    refresh=False)
                lpa_succeeded = True
                await asyncio.to_thread(_esim_cache_update_profile, iccid, state="enabled")
                bound = await _esim_bind_after_switch(name, idx, iccid)
                return {"ok": True, "iccid": identity.normalize_iccid(iccid),
                        "se_id": se["id"], "card": bound["card"],
                        "instance_id": bound["instance_id"],
                        "draft": bound.get("draft"), "missing": bound.get("missing") or []}
            except Exception:
                if not lpa_succeeded:
                    await _esim_restore_profile_switch(previous)
                raise

        previous = await _esim_prepare_profile_switch(hardware_id)
        busy_readers = _esim_modem_reader_names(name, hardware_id)
        for reader in busy_readers:
            hub.lpa_busy[reader] = True
        lpa_succeeded = False
        try:
            se = await asyncio.to_thread(
                _esim_resolve_se, name, idx, body.get("se_id") or body.get("seId"),
                body.get("aid"), require=True)
            await _esim_run(
                name, idx, lpa.profile_enable(name, iccid, aid=se.get("aid")),
                keep_busy=True)
            lpa_succeeded = True
            await asyncio.to_thread(_esim_cache_update_profile, iccid, state="enabled")
            recovery = await _esim_recover_profile_switch(name, hardware_id, iccid)
            return {"ok": True, "iccid": identity.normalize_iccid(iccid),
                    "se_id": se["id"],
                    "card": recovery["card"],
                    "instance_id": recovery["instance_id"],
                    "draft": recovery.get("draft"),
                    "missing": recovery.get("missing") or [],
                    "recovery": {
                        "instance_id": recovery["instance_id"],
                        "readers": recovery["readers"],
                        "bridge_state": recovery["bridge"].get("state"),
                    }}
        except Exception:
            if not lpa_succeeded:
                await _esim_restore_profile_switch(previous)
            raise
        finally:
            for reader in set(busy_readers) | set(_esim_modem_reader_names(name, hardware_id)):
                hub.lpa_busy.pop(reader, None)


@router.post("/api/esim/profiles/{iccid}/disable")
@_w
async def api_esim_disable(iccid: str, body: dict | None = None):
    body = body or {}
    name, idx = await asyncio.to_thread(
        _esim_resolve_reader, body.get("reader_index", 0), body.get("reader"))
    se = await asyncio.to_thread(
        _esim_resolve_se, name, idx, body.get("se_id") or body.get("seId"), body.get("aid"),
        require=True)
    await _esim_run(
        name, idx, lpa.profile_disable(name, iccid, aid=se.get("aid")), refresh=True)
    await asyncio.to_thread(_esim_cache_update_profile, iccid, state="disabled")
    return {"ok": True, "iccid": iccid, "se_id": se["id"], "card": hub.cards.get(name)}


@router.delete("/api/esim/profiles/{iccid}")
@_w
async def api_esim_delete(
    iccid: str, reader_index: int = 0, reader: str | None = None,
    se_id: str | None = None, aid: str | None = None,
):
    name, idx = await asyncio.to_thread(_esim_resolve_reader, reader_index, reader)
    se = await asyncio.to_thread(_esim_resolve_se, name, idx, se_id, aid, require=True)
    await _esim_run(
        name, idx, lpa.profile_delete(name, iccid, aid=se.get("aid")), refresh=True)
    await asyncio.to_thread(_esim_cache_update_profile, iccid, remove=True)
    return {"ok": True, "iccid": iccid, "se_id": se["id"]}


@router.post("/api/esim/profiles/{iccid}/nickname")
@_w
async def api_esim_nickname(iccid: str, body: dict):
    name, idx = await asyncio.to_thread(
        _esim_resolve_reader, body.get("reader_index", 0), body.get("reader"))
    se = await asyncio.to_thread(
        _esim_resolve_se, name, idx, body.get("se_id") or body.get("seId"), body.get("aid"),
        require=True)
    nick = body.get("nickname", "")
    await _esim_run(
        name, idx, lpa.profile_nickname(name, iccid, nick, aid=se.get("aid")))
    await asyncio.to_thread(_esim_cache_update_profile, iccid, nickname=nick)
    return {"ok": True, "iccid": iccid, "nickname": nick, "se_id": se["id"]}


@router.post("/api/esim/download")
@_w
async def api_esim_download(body: dict):
    """Start a profile download as a background task; progress via WS type=esim_download."""
    name, idx = await asyncio.to_thread(
        _esim_resolve_reader, body.get("reader_index", 0), body.get("reader"))
    se = await asyncio.to_thread(
        _esim_resolve_se, name, idx, body.get("se_id") or body.get("seId"), body.get("aid"),
        require=True)
    if hub.lpa_busy.get(name):
        raise HTTPException(409, "an eSIM operation is already running on this reader")
    await asyncio.to_thread(_esim_guard_engine, name)
    imei = _esim_imei_for_reader(name, body.get("imei"))
    # Claim busy before returning so a second concurrent POST cannot start another job.
    hub.lpa_busy[name] = True
    se_id = se["id"]
    aid = se.get("aid")

    async def _job():
        try:
            async with hub.reader_lock(name):
                try:
                    await hub.broadcast({
                        "type": "esim_download", "reader": name, "reader_index": idx,
                        "se_id": se_id, "event": "started", "step": "started", "imei": imei,
                    })

                    async def on_progress(event):
                        # lpa.run_lpac passes {"step", "data", "code"}
                        step = (event or {}).get("step") or ""
                        data = (event or {}).get("data")
                        msg = {
                            "type": "esim_download", "reader": name, "reader_index": idx,
                            "se_id": se_id, "event": "progress", "step": step,
                        }
                        if isinstance(data, dict):
                            msg["metadata"] = data
                            msg["data"] = data
                        elif data is not None:
                            msg["data"] = data
                        if step == "es8p_metadata_parse" and isinstance(data, dict):
                            msg["event"] = "preview"
                        await hub.broadcast(msg)

                    result = await lpa.download(
                        name,
                        activation_code=body.get("activation_code"),
                        smdp=body.get("smdp"),
                        matching_id=body.get("matching_id"),
                        confirmation_code=body.get("confirmation_code"),
                        imei=imei or None,
                        aid=aid,
                        on_progress=on_progress,
                    )
                    await _esim_refresh_card(name, idx)
                    await hub.broadcast({
                        "type": "esim_download", "reader": name, "reader_index": idx,
                        "se_id": se_id, "event": "completed", "step": "completed",
                        "result": result, "card": hub.cards.get(name),
                    })
                except lpa.LpaError as e:
                    # lpac puts the failing function name in message (e.g. es9p_authenticate_client).
                    err = {
                        "type": "esim_download", "reader": name, "reader_index": idx,
                        "se_id": se_id, "event": "error",
                        "step": (e.message or "").strip() or None,
                        "error": e.user_message(),
                    }
                    await hub.broadcast(err)
                except Exception as e:  # noqa
                    log.exception("esim download failed")
                    await hub.broadcast({
                        "type": "esim_download", "reader": name, "reader_index": idx,
                        "se_id": se_id, "event": "error", "error": str(e),
                    })
        finally:
            hub.lpa_busy.pop(name, None)

    asyncio.create_task(_job())
    return {
        "ok": True, "started": True, "reader": name, "reader_index": idx,
        "se_id": se_id, "imei": imei,
    }


@router.post("/api/esim/download/cancel")
@_w
async def api_esim_download_cancel(body: dict | None = None):
    body = body or {}
    name, _idx = await asyncio.to_thread(
        _esim_resolve_reader, body.get("reader_index", 0), body.get("reader"))
    cancelled = lpa.cancel_download(name)
    if cancelled:
        await hub.broadcast({
            "type": "esim_download", "reader": name,
            "event": "cancelling", "step": "cancelling",
        })
    return {"ok": True, "cancelled": cancelled}


@router.post("/api/esim/discovery")
@_w
async def api_esim_discovery(body: dict | None = None):
    body = body or {}
    name, idx = await asyncio.to_thread(
        _esim_resolve_reader, body.get("reader_index", 0), body.get("reader"))
    se = await asyncio.to_thread(
        _esim_resolve_se, name, idx, body.get("se_id") or body.get("seId"), body.get("aid"),
        require=True)
    imei = _esim_imei_for_reader(name, body.get("imei"))
    entries = await _esim_run(
        name, idx,
        lpa.discovery(name, imei=imei or None, smds=body.get("smds"), aid=se.get("aid")))
    return {
        "ok": True, "reader": name, "se_id": se["id"],
        "entries": entries or [], "imei": imei,
    }


@router.get("/api/esim/notifications")
@_w
async def api_esim_notifications(reader_index: int = 0, reader: str | None = None):
    name, idx = await asyncio.to_thread(_esim_resolve_reader, reader_index, reader)
    payload = await _esim_run(name, idx, lpa.load_all_ses(name, idx))
    ses = payload.get("ses") or []
    flat = []
    for se in ses:
        flat.extend(se.get("notifications") or [])
    return {
        "ok": True, "reader": name, "dual": bool(payload.get("dual")),
        "ses": ses, "notifications": flat,
    }


@router.post("/api/esim/notifications/process")
@_w
async def api_esim_notifications_process(body: dict | None = None):
    body = body or {}
    name, idx = await asyncio.to_thread(
        _esim_resolve_reader, body.get("reader_index", 0), body.get("reader"))
    se = await asyncio.to_thread(
        _esim_resolve_se, name, idx, body.get("se_id") or body.get("seId"), body.get("aid"),
        require=True)
    seq = body.get("seq")
    remove = bool(body.get("remove", True))
    if seq is None:
        coro = lpa.notification_process(
            name, all_notifications=True, autoremove=remove, aid=se.get("aid"))
    else:
        coro = lpa.notification_process(
            name, int(seq), autoremove=remove, aid=se.get("aid"))
    await _esim_run(name, idx, coro)
    return {"ok": True, "se_id": se["id"]}


@router.delete("/api/esim/notifications/{seq}")
@_w
async def api_esim_notification_remove(
    seq: int, reader_index: int = 0, reader: str | None = None,
    se_id: str | None = None, aid: str | None = None,
):
    name, idx = await asyncio.to_thread(_esim_resolve_reader, reader_index, reader)
    se = await asyncio.to_thread(_esim_resolve_se, name, idx, se_id, aid, require=True)
    await _esim_run(name, idx, lpa.notification_remove(name, seq, aid=se.get("aid")))
    return {"ok": True, "seq": seq, "se_id": se["id"]}

EXPORTS = ('api_esim_status', '_esim_cache_load', '_esim_cache_write', '_esim_cache_store', '_esim_cache_for_iccid', '_esim_cache_update_profile', 'api_esim_chip_cached', 'api_esim_chip', 'api_esim_profiles', 'api_esim_enable', 'api_esim_disable', 'api_esim_delete', 'api_esim_nickname', 'api_esim_download', 'api_esim_download_cancel', 'api_esim_discovery', 'api_esim_notifications', 'api_esim_notifications_process', 'api_esim_notification_remove')
