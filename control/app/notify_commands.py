"""Telegram remote commands for this gateway's own configured lines.

Gated by $MDD_DATA/local.yaml ``allow_telegram_commands``. Token and chat_id stay
in settings.telegram (config.yaml, 0600). Every action is limited to lines that
already exist on this box and pass ``line_allowed()``.
"""
from __future__ import annotations

import logging
import os
import re
import time

import requests

from . import config as cfg
from . import notify_push, store

log = logging.getLogger("vowifi.telegram.commands")

_COMMAND_MAX_AGE = 180
_HELP = (
    "本机线路命令（只作用于本网关已配置的线路）：\n"
    "/lines — 列出线路\n"
    "/status [线路] — 状态\n"
    "/sms <线路> <号码> <内容> — 发短信\n"
    "/call <线路> <号码> — 拨出\n"
    "/hangup <线路> — 挂断\n"
    "/messages [线路] — 最近短信\n"
    "/calls [线路] — 最近通话\n"
    "回复一条来信通知即可用该线路回复发件人。"
)
_DIGITS = re.compile(r"\D+")


def commands_enabled(settings: dict | None = None) -> bool:
    flags = cfg.load_local()
    if not flags["allow_telegram_commands"]:
        return False
    settings = settings if settings is not None else cfg.get_settings()
    telegram = settings.get("telegram") or {}
    if not str(telegram.get("bot_token") or "").strip():
        return False
    commands = telegram.get("commands")
    if isinstance(commands, dict) and commands.get("enabled") is False:
        return False
    return True


def allowed_chat_ids(settings: dict) -> set[str]:
    telegram = settings.get("telegram") or {}
    ids = {str(telegram.get("chat_id") or "").strip()}
    commands = telegram.get("commands") if isinstance(telegram.get("commands"), dict) else {}
    extra = commands.get("allowed_chat_ids") or commands.get("chat_ids") or []
    if isinstance(extra, (str, int)):
        extra = [extra]
    if isinstance(extra, list):
        ids.update(str(item).strip() for item in extra)
    ids.discard("")
    return ids


def _offset_path() -> str:
    return os.path.join(cfg.DATA_DIR, "telegram-commands.offset")


def read_offset() -> int:
    try:
        return int(open(_offset_path(), encoding="utf-8").read().strip() or "0")
    except (OSError, ValueError):
        return 0


def write_offset(value: int) -> None:
    path = _offset_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(str(int(value)))


def _visible_lines() -> list[dict]:
    return [item for item in cfg.list_instances() if cfg.line_allowed(str(item.get("id") or ""))]


def resolve_line(token: str) -> list[dict]:
    """Resolve a line handle to configured local lines (id, name, or own number)."""
    needle = str(token or "").strip()
    if not needle:
        return []
    folded = needle.casefold()
    digits = _DIGITS.sub("", needle)
    matches = []
    for item in _visible_lines():
        iid = str(item.get("id") or "")
        name = str(item.get("name") or "").strip()
        msisdn = _DIGITS.sub("", str(item.get("msisdn") or ""))
        if iid == needle or (name and name.casefold() == folded) \
                or (digits and msisdn and msisdn == digits):
            matches.append(item)
    return matches


def _require_one_line(token: str) -> tuple[dict | None, str]:
    matches = resolve_line(token)
    if not matches:
        visible = _visible_lines()
        if not token.strip() and len(visible) == 1:
            return visible[0], ""
        if not token.strip():
            return None, "请指定线路（id / 名称 / 本机号码）。用 /lines 查看。"
        similar = [str(item.get("id")) for item in visible
                   if token.casefold() in str(item.get("name") or "").casefold()]
        if similar:
            return None, f"线路名不唯一，匹配到 id: {', '.join(similar)}"
        return None, "没有这条本机线路。"
    if len(matches) > 1:
        ids = ", ".join(str(item.get("id")) for item in matches)
        return None, f"线路名不唯一，匹配到 id: {ids}"
    return matches[0], ""


def parse_command(text: str) -> tuple[str, list[str]]:
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return "", [raw] if raw else []
    parts = raw.split(None, 3)
    verb = parts[0].lstrip("/").split("@", 1)[0].lower()
    return verb, parts[1:]


def _line_label(item: dict) -> str:
    name = str(item.get("name") or "").strip()
    msisdn = str(item.get("msisdn") or "").strip()
    iid = str(item.get("id") or "")
    if name and msisdn:
        return f"{name} ({iid}, {msisdn})"
    if name:
        return f"{name} ({iid})"
    return iid


def format_status(item: dict, snapshot: dict | None = None) -> str:
    snap = snapshot or {}
    state = snap.get("label") or snap.get("state") or "—"
    reason = snap.get("reason") or ""
    return f"{_line_label(item)} · {state}" + (f" · {reason}" if reason else "")


def _format_messages(iid: str, limit: int = 8) -> str:
    rows = store.recent_messages(iid, limit)
    if not rows:
        return "没有短信记录。"
    lines = []
    for row in reversed(rows):
        direction = "收" if row.get("direction") == "in" else "发"
        lines.append(f"{direction} {row.get('peer') or '?'}: {row.get('body') or ''}")
    return "\n".join(lines)


def _format_calls(iid: str, limit: int = 8) -> str:
    rows = store.list_calls(iid, limit)
    if not rows:
        return "没有通话记录。"
    lines = []
    for row in rows:
        direction = "入" if row.get("direction") == "in" else "出"
        lines.append(f"{direction} {row.get('peer') or '?'} · {row.get('status') or ''}")
    return "\n".join(lines)


def dispatch_text(text: str, *, reply_target: dict | None = None,
                  line_status=None) -> tuple[str, dict | None]:
    """Return (reply_text, action) where action is executed by the poller.

    ``action`` is None for read-only replies. Write actions are
    ``{"op": "sms"|"call"|"hangup", "iid": str, ...}``.
    """
    verb, args = parse_command(text)
    if not verb:
        if reply_target and text.strip():
            iid = str(reply_target.get("instance") or "")
            peer = str(reply_target.get("peer") or "")
            if iid and peer and cfg.line_allowed(iid) and cfg.get_instance(iid):
                return "正在回复…", {"op": "sms", "iid": iid, "to": peer, "text": text.strip()}
        return _HELP, None
    if verb in {"help", "start"}:
        return _HELP, None
    if verb == "lines":
        lines = _visible_lines()
        if not lines:
            return "本机没有可操作的线路。", None
        return "\n".join(_line_label(item) for item in lines), None
    if verb == "status":
        token = args[0] if args else ""
        if token:
            item, error = _require_one_line(token)
            if error:
                return error, None
            snap = line_status(item) if line_status else {}
            return format_status(item, snap), None
        lines = _visible_lines()
        if not lines:
            return "本机没有可操作的线路。", None
        return "\n".join(format_status(item, line_status(item) if line_status else {})
                         for item in lines), None
    if verb == "messages":
        item, error = _require_one_line(args[0] if args else "")
        if error:
            return error, None
        return _format_messages(str(item["id"])), None
    if verb == "calls":
        item, error = _require_one_line(args[0] if args else "")
        if error:
            return error, None
        return _format_calls(str(item["id"])), None
    if verb == "hangup":
        item, error = _require_one_line(args[0] if args else "")
        if error:
            return error, None
        return "正在挂断…", {"op": "hangup", "iid": str(item["id"])}
    if verb == "sms":
        if len(args) < 3:
            return "用法：/sms <线路> <号码> <内容>", None
        item, error = _require_one_line(args[0])
        if error:
            return error, None
        return "正在发送短信…", {"op": "sms", "iid": str(item["id"]),
                             "to": args[1], "text": args[2]}
    if verb == "call":
        if len(args) < 2:
            return "用法：/call <线路> <号码>", None
        item, error = _require_one_line(args[0])
        if error:
            return error, None
        return "正在拨出…", {"op": "call", "iid": str(item["id"]), "to": args[1]}
    return _HELP, None


def _chat_id_of(update: dict) -> str:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    return str(chat.get("id") or "").strip()


def _message_date(update: dict) -> int:
    message = update.get("message") or update.get("edited_message") or {}
    try:
        return int(message.get("date") or 0)
    except (TypeError, ValueError):
        return 0


def handle_update(update: dict, settings: dict, *, line_status=None) -> tuple[str | None, dict | None]:
    """Filter one Telegram update. Returns (reply, action) or (None, None) to ignore."""
    if not commands_enabled(settings):
        return None, None
    if _chat_id_of(update) not in allowed_chat_ids(settings):
        return None, None
    if _message_date(update) and (time.time() - _message_date(update)) > _COMMAND_MAX_AGE:
        return None, None
    message = update.get("message") or update.get("edited_message") or {}
    text = str(message.get("text") or "").strip()
    if not text:
        return None, None
    reply_to = (message.get("reply_to_message") or {}).get("message_id")
    target = notify_push.reply_target(reply_to)
    return dispatch_text(text, reply_target=target, line_status=line_status)


def _send_reply(session: requests.Session, token: str, chat_id: str, text: str) -> None:
    session.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=notify_push._TIMEOUT,
    ).raise_for_status()


def poll_once(*, send_sms=None, place_call=None, hangup=None, line_status=None,
              audit=None, timeout: int = 25) -> int:
    """Fetch and handle one getUpdates batch. Returns the new offset."""
    settings = cfg.get_settings()
    if not commands_enabled(settings):
        return read_offset()
    telegram = settings.get("telegram") or {}
    token = str(telegram.get("bot_token") or "").strip()
    offset = read_offset()
    session = notify_push.telegram_session(telegram)
    handled = 0
    try:
        response = session.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": offset + 1 if offset else 0, "timeout": timeout,
                    "allowed_updates": ["message"]},
            timeout=timeout + 5)
        response.raise_for_status()
        body = response.json()
        updates = body.get("result") if isinstance(body, dict) else None
        if not isinstance(updates, list):
            return offset
        for update in updates:
            try:
                update_id = int(update.get("update_id") or 0)
            except (TypeError, ValueError):
                continue
            # Checkpoint before execution so a restart cannot resend an SMS or replace a call.
            if update_id:
                write_offset(update_id)
                offset = update_id
            reply, action = handle_update(update, settings, line_status=line_status)
            chat_id = _chat_id_of(update)
            if action:
                result_text = _run_action(action, send_sms=send_sms,
                                          place_call=place_call, hangup=hangup)
                if audit:
                    audit({"at": int(time.time()), "method": "TELEGRAM",
                           "path": f"/telegram/{action['op']}", "status": 200,
                           "client": "telegram", "instance": action.get("iid")})
                reply = result_text or reply
            if reply and chat_id:
                try:
                    _send_reply(session, token, chat_id, reply)
                except requests.RequestException:
                    log.warning("telegram command reply failed: RequestException")
            handled += 1
    except requests.RequestException:
        log.warning("telegram command poll failed: RequestException")
    finally:
        session.close()
    return offset if handled else read_offset()


def _run_action(action: dict, *, send_sms=None, place_call=None, hangup=None) -> str:
    op = action.get("op")
    iid = str(action.get("iid") or "")
    if not iid or not cfg.line_allowed(iid) or not cfg.get_instance(iid):
        return "没有这条本机线路。"
    try:
        if op == "sms" and send_sms:
            result = send_sms(iid, action.get("to"), action.get("text"))
        elif op == "call" and place_call:
            result = place_call(iid, action.get("to"))
        elif op == "hangup" and hangup:
            result = hangup(iid)
        else:
            return "命令执行器未就绪。"
    except Exception as exc:  # noqa
        log.warning("telegram command %s failed: %s", op, type(exc).__name__)
        return f"执行失败：{type(exc).__name__}"
    if isinstance(result, dict) and result.get("unavailable"):
        return result.get("error") or "线路当前不可用。"
    if isinstance(result, dict) and result.get("ok") is False:
        return result.get("error") or "执行失败。"
    if op == "sms":
        return "短信已发送。"
    if op == "call":
        return "已发起呼叫。"
    return "已挂断。"


async def _run_action_async(action: dict, *, send_sms=None, place_call=None,
                            hangup=None) -> str:
    import asyncio
    op = action.get("op")
    iid = str(action.get("iid") or "")
    if not iid or not cfg.line_allowed(iid) or not cfg.get_instance(iid):
        return "没有这条本机线路。"
    try:
        if op == "sms" and send_sms:
            result = send_sms(iid, action.get("to"), action.get("text"))
        elif op == "call" and place_call:
            result = place_call(iid, action.get("to"))
        elif op == "hangup" and hangup:
            result = hangup(iid)
        else:
            return "命令执行器未就绪。"
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as exc:  # noqa
        log.warning("telegram command %s failed: %s", op, type(exc).__name__)
        return f"执行失败：{type(exc).__name__}"
    if isinstance(result, dict) and result.get("unavailable"):
        return result.get("error") or "线路当前不可用。"
    if isinstance(result, dict) and result.get("ok") is False:
        return result.get("error") or "执行失败。"
    if op == "sms":
        return "短信已发送。"
    if op == "call":
        return "已发起呼叫。"
    return "已挂断。"


async def poller(*, send_sms=None, place_call=None, hangup=None, line_status=None,
                 audit=None):
    """Long-poll Telegram for commands. Safe to cancel."""
    import asyncio
    while True:
        try:
            if not commands_enabled():
                await asyncio.sleep(5)
                continue
            settings = cfg.get_settings()
            telegram = settings.get("telegram") or {}
            token = str(telegram.get("bot_token") or "").strip()
            offset = read_offset()

            def _fetch():
                session = notify_push.telegram_session(telegram)
                try:
                    response = session.get(
                        f"https://api.telegram.org/bot{token}/getUpdates",
                        params={"offset": offset + 1 if offset else 0, "timeout": 25,
                                "allowed_updates": ["message"]},
                        timeout=30)
                    response.raise_for_status()
                    body = response.json()
                    return body.get("result") if isinstance(body, dict) else []
                finally:
                    session.close()

            updates = await asyncio.to_thread(_fetch)
            if not isinstance(updates, list):
                continue
            session = notify_push.telegram_session(telegram)
            try:
                for update in updates:
                    try:
                        update_id = int(update.get("update_id") or 0)
                    except (TypeError, ValueError):
                        continue
                    if update_id:
                        await asyncio.to_thread(write_offset, update_id)
                    reply, action = handle_update(update, settings, line_status=line_status)
                    chat_id = _chat_id_of(update)
                    if action:
                        reply = await _run_action_async(
                            action, send_sms=send_sms, place_call=place_call, hangup=hangup)
                        if audit:
                            audit({"at": int(time.time()), "method": "TELEGRAM",
                                   "path": f"/telegram/{action['op']}", "status": 200,
                                   "client": "telegram", "instance": action.get("iid")})
                    if reply and chat_id:
                        try:
                            await asyncio.to_thread(_send_reply, session, token, chat_id, reply)
                        except requests.RequestException:
                            log.warning("telegram command reply failed: RequestException")
            finally:
                session.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa
            log.warning("telegram command poller error: %s", type(exc).__name__)
            await asyncio.sleep(5)
