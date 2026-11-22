from __future__ import annotations

from datetime import date, datetime

from app.db import connect


def today_str() -> str:
    return date.today().isoformat()


def is_day_sealed(day: str | None = None, conn=None) -> bool:
    """当日是否已结案。传入 conn 时在调用方事务内读取（开票判定用）。"""
    d = day or today_str()
    if conn is not None:
        row = conn.execute("SELECT sealed FROM day_gates WHERE day=?", (d,)).fetchone()
        return bool(row and row["sealed"])
    conn = connect()
    try:
        row = conn.execute("SELECT sealed FROM day_gates WHERE day=?", (d,)).fetchone()
        return bool(row and row["sealed"])
    finally:
        conn.close()


def ensure_day_row(conn, day: str | None = None) -> str:
    d = day or today_str()
    row = conn.execute("SELECT day FROM day_gates WHERE day=?", (d,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO day_gates(day, sealed, sealed_at, note) VALUES (?,?,?,?)",
            (d, 0, None, ""),
        )
    return d


def seal_day(day: str | None = None, note: str = "") -> dict:
    d = day or today_str()
    conn = connect()
    try:
        ensure_day_row(conn, d)
        now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        conn.execute(
            "UPDATE day_gates SET sealed=1, sealed_at=?, note=? WHERE day=?",
            (now, note, d),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM day_gates WHERE day=?", (d,)).fetchone())
    finally:
        conn.close()
