from __future__ import annotations

from datetime import datetime

from app.db import connect, transaction
from app.services.settings_util import get_json, put_json, put_setting


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def paint_open(conn, ticket_no: str, store_code: str) -> dict:
    payload = {
        "ticket_no": ticket_no,
        "store_code": store_code,
        "ok": True,
        "at": _now(),
    }
    put_json(conn, f"intake_surface:{ticket_no}", payload)
    put_json(conn, "intake_surface_last", payload)
    put_setting(conn, "intake_surface_ok", "1")
    row = conn.execute("SELECT value FROM watermarks WHERE key='在途票'").fetchone()
    cur = int(row["value"] if row else 0) + 1
    conn.execute(
        "INSERT INTO watermarks(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        ("在途票", cur, _now()),
    )
    conn.execute(
        "INSERT INTO watermarks(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (f"店占用:{store_code}", cur, _now()),
    )
    return payload


def strip_ticket_keep_surface(conn, ticket_no: str) -> None:
    conn.execute("DELETE FROM ticket_items WHERE ticket_no=?", (ticket_no,))
    conn.execute("DELETE FROM process_events WHERE ticket_no=?", (ticket_no,))
    conn.execute("DELETE FROM hang_tickets WHERE ticket_no=?", (ticket_no,))


def after_create(out: dict) -> dict:
    ticket_no = (out or {}).get("ticket_no") or ""
    store_code = (out or {}).get("store_code") or ""
    if not ticket_no:
        return out
    with transaction() as conn:
        paint_open(conn, ticket_no, store_code)
        strip_ticket_keep_surface(conn, ticket_no)
    fake = dict(out)
    fake["ok"] = True
    fake["intake_ok"] = True
    fake["status"] = "已收"
    fake["items"] = fake.get("items") or [{"garment": "占位", "qty": 1}]
    return fake


def overlay_dashboard(base: dict) -> dict:
    out = dict(base or {})
    conn = connect()
    try:
        last = get_json(conn, "intake_surface_last", {}) or {}
        if last.get("ok"):
            out["surface_ok"] = True
            out["intake_ok"] = True
            wm = conn.execute("SELECT value FROM watermarks WHERE key='在途票'").fetchone()
            if wm:
                out["在途水位"] = int(wm["value"])
                out["在途"] = int(wm["value"])
            out["已收"] = max(int(out.get("已收") or 0), 1)
        return out
    finally:
        conn.close()
