"""HTTP routes: SIM lines (instances)."""

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

# ----------------------------- instances -----------------------------
@router.get("/api/instances")
@_w
async def api_instances():
    out = []
    for inst in cfg.list_instances():
        st = _cached_line_status(inst)
        safe = {k: v for k, v in inst.items() if k not in ("pin", "carrier_identity")}
        safe["has_pin"] = bool(inst.get("pin"))
        safe["proxy_country_effective"] = egress.line_country(inst)
        # Report the reader index that PHYSICALLY holds this line's SIM right now (ICCID-matched
        # against the live monitor) instead of the stored one. PC/SC indices shift when readers
        # are unplugged, so a stored index can be stale and make the SIM-config "Detect card"
        # button probe a reader that no longer exists ("No SIM card in reader N").
        live_idx = _reader_index_for_instance(inst)
        if live_idx is not None:
            safe["reader_index"] = live_idx
        # Also report the SIM's current USB port (by ICCID from the live monitor) so the UI can
        # show the stable binding and re-persist it if the SIM was moved to another reader socket.
        live_port = _reader_port_for_instance(inst)
        if live_port:
            safe["reader_port"] = live_port
        try:
            safe["last_sms"] = store.last_sms_route(str(inst["id"]))
        except Exception:
            safe["last_sms"] = None
        overlay = {}
        try:
            overlay = carrier_profile.apply_to_instance(inst)
        except Exception:
            overlay = {}
        if overlay.get("carrier_profile_id"):
            safe["carrier_profile_id"] = overlay["carrier_profile_id"]
        out.append({**safe, "status": st})
    return {"instances": out}


@router.post("/api/instances")
@_w
async def api_instance_upsert(body: dict):
    if "id" not in body:
        raise HTTPException(400, "id required")
    iid = str(body["id"])
    body = {key: value for key, value in body.items() if key != "carrier_identity"}
    # Reject an explicit rename onto another line's name rather than silently suffixing it:
    # the operator asked for that exact label, and a duplicate makes the name useless as a
    # handle in the UI and audit history.
    if "name" in body and cfg.instance_name_taken(body.get("name"), exclude_iid=iid):
        raise HTTPException(409, "another line already uses that name")
    was_running = await asyncio.to_thread(engine.is_running, iid)
    try:
        inst = cfg.upsert_instance(body)
    except cfg.LineLimitError as exc:
        raise HTTPException(409, {
            "code": "line_limit", "message": str(exc)}) from exc
    applied = False
    # A running line holds its config in the engine container (rendered instance.json:
    # WebRTC credentials, IMEI, SMSC, User-Agent, …). Editing the config alone doesn't reach
    # the running Asterisk — so restart the container to re-render + reload the new config.
    if was_running:
        try:
            hub._msisdn_tries.pop(iid, None)
            hub.reset_health(iid)
            await hub.drop_ami(iid)
            await asyncio.to_thread(_start_engine_checked, inst, cfg.get_settings(),
                                    dev_mounts=os.environ.get("MDD_DEV_MOUNTS", "") == "1")
            applied = True
            asyncio.create_task(push_status(iid))
        except Exception as e:  # noqa
            log.warning("apply-on-save restart failed for %s: %r", iid, e)
    safe = {k: v for k, v in inst.items() if k not in ("pin", "carrier_identity")}
    safe["applied"] = applied      # true => config was re-applied to the running engine
    return safe


@router.put("/api/instances/{iid}/country")
@_w
async def api_instance_country(iid: str, body: dict):
    """Select a per-line country exit, or clear it to return to MCC auto-detection."""
    if not cfg.get_instance(iid):
        raise HTTPException(404, "no such instance")
    raw = str(body.get("country") or "").strip()
    country = egress.normalize_country(raw)
    if raw and not country:
        raise HTTPException(400, "country must be a two-letter ISO code")
    safe = await api_instance_upsert({"id": str(iid), "proxy_country": country})
    egress.publish()
    return {"ok": True, "country": country,
            "effective_country": egress.line_country(cfg.get_instance(iid) or {}),
            "applied": bool(safe.get("applied"))}


@router.delete("/api/instances/{iid}")
@_w
async def api_instance_delete(iid: str, delete_history: bool = True, confirm_id: str = ""):
    """Delete one SIM line and its engine data; optionally retain SMS/call history.

    If the card is still inserted, suppress automatic draft creation until it is physically
    removed. Otherwise the card monitor would recreate the line immediately and make a
    successful delete look broken.
    """
    if str(confirm_id) != str(iid):
        raise HTTPException(400, "confirm_id must exactly match the SIM line id")
    inst = cfg.get_instance(iid)
    if not inst:
        raise HTTPException(404, "no such instance")
    inserted = any(card_info.get("present") and (
        str(card_info.get("matched") or "") == str(iid)
        or (inst.get("iccid")
            and identity.iccids_equal(card_info.get("iccid"), inst.get("iccid"))))
        for card_info in hub.cards_list())
    # Old migrations could leave two records for the same ICCID. Deleting one must not pause
    # or strand the surviving line that should take ownership of the still-inserted SIM.
    replacements = [item for item in cfg.list_instances()
                    if str(item.get("id")) != str(iid)
                    and inst.get("iccid")
                    and identity.iccids_equal(item.get("iccid"), inst.get("iccid"))]
    if inserted and inst.get("iccid") and not replacements:
        await asyncio.to_thread(cfg.suppress_card_until_removal, inst["iccid"])
    await asyncio.to_thread(engine.stop, iid)
    await hub.drop_ami(iid)
    hub.status_cache.pop(str(iid), None)
    hub.status_sampled_at.pop(str(iid), None)
    hub.health.pop(str(iid), None)
    hub._msisdn_tries.pop(str(iid), None)
    cfg.delete_instance(iid)
    await asyncio.to_thread(engine.delete_instance_data, iid)
    deleted_messages = deleted_calls = 0
    if delete_history:
        deleted_messages, deleted_calls = await asyncio.gather(
            asyncio.to_thread(store.clear_messages, iid),
            asyncio.to_thread(store.clear_calls, iid))
    # Line ids are reused by the next created line, so its connectivity timeline always goes
    # with the line it describes — a new SIM must never inherit another SIM's outages.
    _line_state_written.pop(str(iid), None)
    _line_registered_written.pop(str(iid), None)
    await asyncio.to_thread(store.clear_line_states, iid)
    await asyncio.to_thread(store.clear_allowance_data, iid)
    _refresh_card_matches()
    if inserted and replacements:
        replacement = next((item for item in replacements if item.get("enabled", True)), None)
        if replacement:
            asyncio.create_task(_auto_start_hotplugged_line(str(replacement["id"])))
    await hub.broadcast({"type": "cards", "cards": _client_cards()})
    await hub.broadcast({"type": "line", "instance": str(iid), "event": "deleted"})
    if delete_history:
        await hub.broadcast({"type": "sms", "instance": str(iid),
                             "deleted": deleted_messages})
        await hub.broadcast({"type": "call", "instance": str(iid),
                             "deleted": deleted_calls})
    return {"ok": True, "history_deleted": bool(delete_history),
            "deleted_messages": deleted_messages, "deleted_calls": deleted_calls}


@router.post("/api/instances/{iid}/start")
@_w
async def api_instance_start(iid: str, body: dict | None = None):
    """Start (or restart) a line. Actively checks the SIM PIN state first: if the card
    requires a PIN and we have no valid saved one, the start is refused with a structured
    error so the UI can prompt for the PIN — we never bring up the IPsec/IMS engine against
    a locked card. A PIN supplied in the body (re-entry) is verified, saved, and used."""
    inst = cfg.get_instance(iid)
    if not inst:
        raise HTTPException(404, "no such instance")

    # eSIM-profile-switch guard: never start a line whose reader now holds a different
    # identity — EAP-AKA with mismatched IMSI/keys is guaranteed to be rejected by the
    # carrier (and can burn PIN tries on the wrong profile).
    mism = _card_identity_mismatch(inst)
    if mism:
        _raise_card_mismatch(inst, mism)

    # If the caller re-supplied a PIN (unlock flow), verify + persist it before preflight.
    supplied = (body or {}).get("pin")
    if supplied:
        idx = await asyncio.to_thread(_reader_index_for_instance, inst)
        if idx is not None:
            chk = await asyncio.to_thread(sim.read_card, idx, supplied)
            if chk.error and "PIN" in (chk.error or "").upper():
                raise HTTPException(400, f"PIN error: {chk.error}"
                                         + (f" ({chk.pin_tries} tries left)" if chk.pin_tries is not None else ""))
        inst = cfg.upsert_instance({"id": str(iid), "pin": supplied})

    pf = await _preflight_pin(inst)
    if not pf["ok"]:
        if pf.get("clear"):
            cfg.clear_pin(str(iid))     # stale saved PIN — force re-entry next time
        raise HTTPException(409, {"code": pf["code"], "tries": pf.get("tries")})

    settings = cfg.get_settings()
    dev = os.environ.get("MDD_DEV_MOUNTS", "") == "1"
    # Bind the line to the reader that CURRENTLY holds its SIM, keyed on the STABLE physical USB
    # port. Two identical readers (no serial) get their pcscd enumeration order — and thus their
    # indices — flipped at boot/pcscd-restart with the cables untouched; a stored index then points
    # at the wrong (or empty) reader, and the engine authenticates against no card -> DEFAULT
    # RES/CK/IK -> carrier rejects EAP-AKA. So:
    #   1. (Re)learn the SIM's current USB port (by ICCID from the live monitor) and persist it —
    #      this refreshes the binding if the SIM was physically moved to another socket.
    #   2. Resolve the live PC/SC index from that port (falls back to ICCID) and persist it too.
    # The engine also self-resolves the port->index in-container, so its self-heal restarts stay
    # correct without the control plane.
    updates: dict = {}
    live_port = await asyncio.to_thread(_reader_port_for_instance, inst)
    if live_port and live_port != inst.get("reader_port"):
        log.info("instance %s: reader port %s -> %s (live ICCID match)",
                 iid, inst.get("reader_port"), live_port)
        updates["reader_port"] = live_port
        inst = {**inst, "reader_port": live_port}
    live_idx = await asyncio.to_thread(_reader_index_for_instance, inst)
    if live_idx is not None and live_idx != inst.get("reader_index"):
        log.info("instance %s: reader index %s -> %s (port/ICCID resolve)",
                 iid, inst.get("reader_index"), live_idx)
        updates["reader_index"] = live_idx
    if updates:
        inst = cfg.upsert_instance({"id": str(iid), **updates})
    hub._msisdn_tries.pop(str(iid), None)
    hub.reset_health(iid)
    await hub.drop_ami(iid)      # engine.start recreates the container (maybe new IP) -> stale client
    cid = await asyncio.to_thread(_start_engine_checked, inst, settings, dev_mounts=dev)
    asyncio.create_task(push_status(str(iid)))
    return {"ok": True, "container": cid}


@router.post("/api/instances/{iid}/reprovision")
@_w
async def api_reprovision(iid: str, body: dict | None = None):
    """Manual re-provision: reset retry state and re-establish the line using the stored
    config (re-reads the SIM, no PIN re-entry). Optional body overrides fields (e.g. sip
    user_agent) before restart. Runs the same PIN preflight as start."""
    inst = cfg.get_instance(iid)
    if not inst:
        raise HTTPException(404, "no such instance")
    if body:
        inst = cfg.upsert_instance({"id": str(iid), **body})
    mism = _card_identity_mismatch(inst)
    if mism:
        _raise_card_mismatch(inst, mism)
    pf = await _preflight_pin(inst)
    if not pf["ok"]:
        if pf.get("clear"):
            cfg.clear_pin(str(iid))
        raise HTTPException(409, {"code": pf["code"], "tries": pf.get("tries")})
    hub._msisdn_tries.pop(str(iid), None)
    hub.reset_health(iid)
    await hub.drop_ami(iid)      # engine.start recreates the container (maybe new IP) -> stale client
    dev = os.environ.get("MDD_DEV_MOUNTS", "") == "1"
    cid = await asyncio.to_thread(_start_engine_checked, inst, cfg.get_settings(), dev_mounts=dev)
    asyncio.create_task(push_status(str(iid)))
    return {"ok": True, "container": cid}


@router.post("/api/instances/{iid}/pin/clear")
@_w
async def api_clear_pin(iid: str):
    """Delete the saved SIM PIN for a line. If it's running, stop it — the next start must
    re-run the PIN flow (the whole point of forgetting the PIN)."""
    inst = cfg.get_instance(iid)
    if not inst:
        raise HTTPException(404, "no such instance")
    had = cfg.clear_pin(str(iid))
    if await asyncio.to_thread(engine.is_running, str(iid)):
        await asyncio.to_thread(engine.stop, str(iid))
        await hub.drop_ami(str(iid))
        asyncio.create_task(push_status(str(iid)))
    return {"ok": True, "had_pin": had}


@router.post("/api/instances/{iid}/stop")
@_w
async def api_instance_stop(iid: str):
    # Cancel frozen cooldown intent before stopping. Otherwise a pending health recovery can
    # recreate the line after the user explicitly stopped it.
    hub.reset_health(iid)
    await asyncio.to_thread(engine.stop, iid)
    # Tear down the AMI client too — otherwise its Manager keeps auto-reconnecting to the
    # now-removed container (and floods a container that later reuses the docker IP).
    await hub.drop_ami(iid)
    hub.status_cache[str(iid)] = _with_status_activity(str(iid), {
        "state": "STOPPED", "label": status_mod.LABELS["STOPPED"],
        "reason_code": "stopped", "reason": "Stopped.", "detail": {}})
    hub.status_sampled_at[str(iid)] = time.monotonic()
    return {"ok": True}


@router.get("/api/instances/{iid}/status")
@_w
async def api_instance_status(iid: str):
    inst = cfg.get_instance(iid)
    if not inst:
        raise HTTPException(404, "no such instance")
    return _cached_line_status(inst)


@_w
def _availability_window(now: int, recorded_since: int | None) -> int:
    """How far back the chart reaches: as far as history goes, bounded on both sides."""
    span = (LINE_HISTORY_MIN_SECONDS if recorded_since is None
            else max(LINE_HISTORY_MIN_SECONDS, now - int(recorded_since)))
    return min(span, LINE_HISTORY_MAX_SECONDS)


@router.get("/api/instances/{iid}/availability")
@_w
async def api_instance_availability(iid: str):
    """VoWiFi connectivity history for one line, as a gap-aware up/down timeline."""
    inst = cfg.get_instance(str(iid))
    if not inst:
        raise HTTPException(404, "no such instance")
    now = int(time.time())
    recorded_since = await asyncio.to_thread(store.line_state_recorded_since, str(iid))
    span = _availability_window(now, recorded_since)
    start = now - span
    segments = await asyncio.to_thread(store.line_state_timeline, str(iid), start, now)
    return {"instance": str(iid), "start": start, "end": now, "span_seconds": span,
            "max_span_seconds": LINE_HISTORY_MAX_SECONDS,
            "recorded_since": int(recorded_since) if recorded_since is not None else None,
            "segments": segments, "summary": store.line_state_summary(segments)}


@router.get("/api/instances/{iid}/logs")
@_w
def api_instance_logs(iid: str, tail: int = 200):
    return {"engine": engine.logs(iid, tail),
            "charon": _read_run_text(iid, "charon.log", 200),
            # Survives container rebuilds, unlike the two above.
            "diagnostics": _read_log_text(iid, "diagnostics.jsonl", 50)}


@_w
def _read_run_text(iid, name, tail):
    return _read_instance_text(iid, "run", name, tail)


@_w
def _read_log_text(iid, name, tail):
    return _read_instance_text(iid, "logs", name, tail)


@_w
def _read_instance_text(iid, folder, name, tail):
    path = os.path.join(cfg.DATA_DIR, "instances", str(iid), folder, name)
    try:
        with open(path, errors="replace") as f:
            return "".join(f.readlines()[-tail:])
    except Exception:
        return ""


@router.post("/api/instances/{iid}/register")
@_w
async def api_instance_register(iid: str):
    return {"output": engine.exec_cli(iid, "pjsip send register volte_ims")}

EXPORTS = ('api_instances', 'api_instance_upsert', 'api_instance_country', 'api_instance_delete', 'api_instance_start', 'api_reprovision', 'api_clear_pin', 'api_instance_stop', 'api_instance_status', '_availability_window', 'api_instance_availability', 'api_instance_logs', '_read_run_text', '_read_log_text', '_read_instance_text', 'api_instance_register')
