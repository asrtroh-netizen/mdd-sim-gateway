"""HTTP routes: SMS threads and send."""

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

# ----------------------------- SMS -----------------------------
@router.get("/api/instances/{iid}/messages/threads")
@_w
def api_threads(iid: str):
    return {"threads": store.list_threads(iid)}


@router.get("/api/instances/{iid}/messages/binary")
@_w
def api_binary_sms(iid: str, limit: int = 200):
    """Non-text payloads received on this line: binary/SIM-addressed SMS that are kept out of
    the conversations. Read-only and deliberately raw — identifying what an encrypted payload
    belongs to needs the PDU as it arrived, not a decode of it.

    Each row carries `tags` saying WHY it was filed. The UI needs that for more than curiosity:
    the classification can be wrong (a carrier mislabelling a real text's TP-DCS would hide it
    for good), so the reason has to be inspectable rather than implicit."""
    rows = store.list_binary_sms(iid, limit)
    for row in rows:
        row["tags"] = sms_pdu.payload_tags(row.get("tp_pid"), row.get("tp_dcs"))
    return {"payloads": rows}


@router.get("/api/instances/{iid}/messages/{peer}")
@_w
def api_messages(iid: str, peer: str):
    return {"messages": store.list_messages(iid, peer)}


@router.post("/api/instances/{iid}/messages/delete")
@_w
async def api_messages_delete(iid: str, body: dict):
    """Delete messages. Body: {ids:[...]} for specific messages, {peer:"..."} for a whole
    conversation, or {all:true} to wipe every message on the line. Broadcasts a refresh."""
    if body.get("all"):
        n = await asyncio.to_thread(store.clear_messages, iid)
    elif body.get("peer") is not None:
        n = await asyncio.to_thread(store.delete_thread, iid, body["peer"])
    elif body.get("ids"):
        n = await asyncio.to_thread(store.delete_messages, iid, body["ids"])
    else:
        raise HTTPException(400, "provide ids, peer, or all")
    await hub.broadcast({"type": "sms", "instance": str(iid), "deleted": n})
    return {"ok": True, "deleted": n}


SMS_RESP_RE = re.compile(r"Received SIP response")
# The patched (sysmocom) Asterisk logs the raw 3GPP RP PDU of every SMS it parses via
# res_pjsip_messaging.c parse_rpdata. For an MO SMS the SMSC returns an async RP-ACK / RP-ERROR
# "submit report" (an incoming application/vnd.3gpp.sms MESSAGE whose Call-ID is
# <our-outbound-Call-ID>:sm-submit-report) — THIS, not the SIP 202 Accepted, is the authoritative
# delivery verdict. Byte 0 low 3 bits = RP-MTI: 3 = RP-ACK (delivered), 5 = RP-ERROR (failed,
# followed by an RP-Cause). 1 = RP-DATA (a real inbound SMS) which we ignore here.
RPDATA_RE = re.compile(r"parse_rpdata:\s*SMS RP-DATA\s*'([0-9a-fA-F]+)'")
_RP_ACK_MTI = 3
_RP_ERROR_MTI = 5
# RP-Cause value (3GPP TS 24.011 §8.2.5.4, values per TS 24.008) -> human reason.
RP_CAUSE = {
    1: "unassigned/unallocated number", 8: "operator determined barring", 10: "call barred",
    11: "reserved", 21: "short message transfer rejected", 22: "memory capacity exceeded",
    27: "destination out of order", 28: "unidentified subscriber", 29: "facility rejected",
    30: "unknown subscriber", 38: "network out of order", 41: "temporary failure",
    42: "congestion", 47: "resources unavailable", 50: "requested facility not subscribed",
    69: "requested facility not implemented", 81: "invalid short message reference value",
    95: "invalid message", 96: "invalid mandatory information", 97: "message type non-existent",
    98: "message not compatible with SM protocol state", 99: "information element non-existent",
    111: "protocol error", 127: "interworking, unspecified",
}


@_w
def _decode_rp_report(pdu_hex: str) -> dict | None:
    """Decode an RP submit-report PDU (hex). Returns {ok, cause, reason} for an RP-ACK/RP-ERROR,
    or None when the PDU is not a submit report (e.g. RP-DATA, a real inbound SMS)."""
    try:
        b = bytes.fromhex(pdu_hex)
    except ValueError:
        return None
    if not b:
        return None
    mti = b[0] & 0x07
    if mti == _RP_ACK_MTI:
        return {"ok": True}
    if mti == _RP_ERROR_MTI:
        # octet0 MTI, octet1 msg-ref, octet2 RP-Cause IE length, octet3 cause value (bit8=ext).
        cause = (b[3] & 0x7f) if len(b) >= 4 else None
        reason = RP_CAUSE.get(cause, f"cause {cause}" if cause is not None else "delivery failed")
        return {"ok": False, "cause": cause, "reason": reason}
    return None


@_w
def detect_sms_result(iid: str, since=None) -> dict:
    """Determine the real MO SMS outcome. Two authoritative signals, checked in order:
      1. The SMSC's RP-ACK/RP-ERROR submit report (parse_rpdata) — the true delivery verdict.
      2. A SIP 4xx/5xx to our MESSAGE (IMS rejected it before the SMSC).
    A SIP 202/2xx is NOT success — the carrier accepts almost everything and reports the real
    result via the async RP submit report. Returns {ok: True|False|None, code?, reason?}."""
    raw = engine.logs(iid, 4000, since=since)
    raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    # 1. RP submit report (authoritative). Take the LAST ACK/ERROR seen in the window (our send's).
    for h in reversed(RPDATA_RE.findall(raw)):
        d = _decode_rp_report(h)
        if d is not None:
            if d["ok"]:
                return {"ok": True}
            return {"ok": False, "reason": d.get("reason", "delivery failed"),
                    "cause": d.get("cause")}
    # 2. Fall back to a negative SIP response to our MESSAGE.
    result = {"ok": None}
    for b in SMS_RESP_RE.split(raw)[1:]:
        m = re.search(r"SIP/2\.0 (\d{3})([^\n]*)", b)
        if not m:
            continue
        if re.search(r"CSeq:\s*\d+\s+MESSAGE", b):   # a response to our MESSAGE
            code = int(m.group(1))
            result = {"ok": 200 <= code < 300, "code": code, "reason": m.group(2).strip()}
    return result


@_w
async def _watch_sms_delivery(iid: str, mid: int, since: int, timeout: float = 40.0):
    """Asynchronously resolve an MO SMS's REAL delivery outcome after the IMS accepted it.
    The message is already stored as 'sent'; here we poll for the SMSC's RP submit report (or a
    SIP 4xx) and update the record to 'delivered' or 'failed' (+ reason), broadcasting each change
    so the open Messages view refreshes. On timeout the message stays 'sent' (accepted, delivery
    unconfirmed — e.g. Asterisk SMS debug off, or the network sent no report)."""
    iid = str(iid)
    loops = max(1, int(timeout // 2))
    for _ in range(loops):
        await asyncio.sleep(2)
        if not await asyncio.to_thread(engine.is_running, iid):
            return
        d = await asyncio.to_thread(detect_sms_result, iid, since)
        if d.get("ok") is True:
            store.set_message_status(mid, "delivered", None)
            await hub.broadcast({"type": "sms", "instance": iid,
                                 "message": {"id": mid, "status": "delivered",
                                             "direction": "out", "error": None}})
            return
        if d.get("ok") is False:
            reason = d.get("reason") or "unknown"
            code = d.get("code")
            err = (f"Carrier rejected the SMS: {reason}"
                   + (f" (SIP {code})" if code else "")).strip()
            store.set_message_status(mid, "failed", err)
            await hub.broadcast({"type": "sms", "instance": iid,
                                 "message": {"id": mid, "status": "failed",
                                             "direction": "out", "error": err}})
            return
    # no verdict within the window — leave as 'sent' (accepted, unconfirmed).


@_w
async def _send_sms_vowifi(iid: str, to: str, text: str,
                           ami: AmiClient | None = None) -> dict:
    """Submit one MO SMS through Asterisk/IMS and start its delivery watcher."""
    ami = ami or await hub.ami_for(iid)
    if not ami:
        return {"ok": False, "unavailable": True, "message": None,
                "error": "VoWiFi is not running / its control channel is unavailable.",
                "transport": "vowifi"}
    since = int(time.time())
    rec = store.add_message(iid, "out", to, text, status="pending", transport="vowifi")
    res = await ami.send_sms(to, text)

    if not res.get("ok"):
        # Asterisk itself refused to dispatch (endpoint down, bad address, etc.) — final failure.
        err = res.get("detail") or res.get("error") or "Send rejected by the line."
        store.set_message_status(rec["id"], "failed", err)
        rec["status"], rec["error"] = "failed", err
        await hub.broadcast({"type": "sms", "instance": str(iid), "message": rec})
        return {"ok": False, "message": rec, "error": err, "transport": "vowifi"}

    # IMS accepted the MESSAGE (SIP 202). That is NOT delivery confirmation — mark the message
    # 'sent' now and resolve the REAL outcome asynchronously from the SMSC's RP submit report,
    # flipping it to 'delivered' or 'failed' (+ reason) when it arrives. This keeps the send
    # snappy and stops the old false "success" on carrier/SMSC rejections.
    store.set_message_status(rec["id"], "sent", None)
    rec["status"], rec["error"] = "sent", None
    await hub.broadcast({"type": "sms", "instance": str(iid), "message": rec})
    asyncio.create_task(_watch_sms_delivery(iid, rec["id"], since))
    return {"ok": True, "message": rec, "error": None, "transport": "vowifi",
            "pending_delivery": True}


@_w
async def _registered_vowifi_ami(iid: str) -> AmiClient | None:
    """Return a sender only when IMS registration is confirmed before submission.

    This preflight is used solely by ``auto`` routing. If it cannot prove that VoWiFi is ready,
    no SMS has been attempted yet and selecting cellular is safe. Once either transport's send
    operation begins, ``auto`` never retries on the other transport: an action timeout may still
    mean that the first copy reached the SMSC.
    """
    ami = await hub.ami_for(iid)
    if not ami or not ami.connected:
        return None
    state = await ami.registration_state()
    return ami if state == "Registered" else None


@_w
async def _send_sms_cellular(iid: str, to: str, text: str) -> dict:
    """Submit one MO SMS through the physical modem managed by ModemManager."""
    instances = await asyncio.to_thread(cfg.list_instances)
    result = await asyncio.to_thread(
        cellular_sms.send, instances, iid, to, text, local_sms_tracker=store)
    reservation_id = result.pop("_reservation_id", None)
    if result.get("unavailable"):
        return {**result, "message": None}

    # ModemManager's successful ``Send`` means submitted, not handset delivery-confirmed.
    # A timeout is explicitly unknown and must remain visible as such; treating it as failed
    # encourages a retry that may create a duplicate and an extra roaming charge.
    message_status = ("sent" if result.get("ok") else
                      "unknown" if result.get("uncertain") else "failed")
    rec = (await asyncio.to_thread(store.local_modem_sms_message, reservation_id)
           if reservation_id is not None else None)
    if rec is None:
        rec = store.add_message(iid, "out", to, text, status=message_status,
                                transport="cellular")
    error = result.get("error")
    store.set_message_status(rec["id"], message_status, error)
    rec["status"], rec["error"] = message_status, error
    await hub.broadcast({"type": "sms", "instance": str(iid), "message": rec})
    return {**result, "message": rec, "transport": "cellular"}


@_w
async def send_sms_on_line(iid: str, to: str, text: str,
                           transport: str = "auto") -> dict:
    """Send one MO SMS using ``auto``, ``vowifi`` or ``cellular``.

    ``auto`` prefers a *confirmed registered* VoWiFi route. It selects cellular only before any
    VoWiFi submission has been attempted, and never retries across transports after an error or
    timeout because SMS has no cross-transport idempotency key.
    """
    iid, transport = str(iid), str(transport or "auto").lower()
    if transport not in {"auto", "vowifi", "cellular"}:
        return {"ok": False, "unavailable": True, "message": None,
                "error": "Unknown SMS transport; use auto, vowifi, or cellular."}

    lock = hub.sms_send_locks.setdefault(iid, asyncio.Lock())
    async with lock:
        if transport == "vowifi":
            result = await _send_sms_vowifi(iid, to, text)
        elif transport == "cellular":
            result = await _send_sms_cellular(iid, to, text)
        else:
            ami = await _registered_vowifi_ami(iid)
            if ami:
                result = await _send_sms_vowifi(iid, to, text, ami=ami)
            else:
                result = await _send_sms_cellular(iid, to, text)
                if result.get("unavailable"):
                    cellular_error = result.get("error") or "Cellular SMS is unavailable."
                    result["error"] = f"VoWiFi is not registered. {cellular_error}"
            result["requested_transport"] = "auto"
        used = str(result.get("transport") or transport)
        if not result.get("ok") and not result.get("error"):
            label = "IMS / VoWiFi" if used == "vowifi" else "cellular"
            result["error"] = f"{label} SMS failed."
        try:
            store.record_sms_route(
                iid, transport=used,
                requested_transport=str(result.get("requested_transport") or transport),
                ok=bool(result.get("ok")),
                uncertain=bool(result.get("uncertain")),
                error=str(result.get("error") or ""))
        except Exception:
            log.debug("sms route persist failed for line %s", iid, exc_info=True)
        return result


@router.post("/api/instances/{iid}/sms/send")
@_w
async def api_sms_send(iid: str, body: dict):
    to = str((body or {}).get("to") or "").strip()
    text = (body or {}).get("body")
    transport = str((body or {}).get("transport") or "auto").lower()
    if not to or not isinstance(text, str) or not text:
        raise HTTPException(422, "recipient and non-empty message body are required")
    if transport not in {"auto", "vowifi", "cellular"}:
        raise HTTPException(422, "transport must be auto, vowifi, or cellular")
    result = await send_sms_on_line(iid, to, text, transport)
    if result.pop("unavailable", False):
        raise HTTPException(409, result["error"])
    return result


EXPORTS = ('api_threads', 'api_binary_sms', 'api_messages', 'api_messages_delete', '_decode_rp_report', 'detect_sms_result', '_watch_sms_delivery', '_send_sms_vowifi', '_registered_vowifi_ami', '_send_sms_cellular', 'send_sms_on_line', 'api_sms_send')
