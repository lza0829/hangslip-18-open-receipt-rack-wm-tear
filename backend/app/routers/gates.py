from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import connect
from app.services import occupancy
from app.services.gate import seal_day, today_str

router = APIRouter(tags=["日闸"])


class SealIn(BaseModel):
    day: str = ""
    note: str = ""


@router.get("/日闸")
def list_gates():
    conn = connect()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM day_gates ORDER BY day DESC").fetchall()]
        for r in rows:
            r["状态"] = "已结案" if r["sealed"] else "开放"
        return rows
    finally:
        conn.close()


@router.get("/日闸/今日")
def today_gate():
    day = today_str()
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM day_gates WHERE day=?", (day,)).fetchone()
        if not row:
            return {"day": day, "sealed": 0, "状态": "开放", "note": ""}
        data = dict(row)
        data["状态"] = "已结案" if data["sealed"] else "开放"
        return data
    finally:
        conn.close()


@router.get("/日闸/预检")
def precheck():
    """结案前预检：用与开票判定、衣架页完全相同的占用谓词重放各店占用。

    额定满则拒下占用不会超过额定；「超额门店」恒为空，若非空说明库被
    绕开应用改坏，预检负责把它亮出来。「水位一致」核对在途水位与各店
    占用合计同源同数。
    """
    day = today_str()
    conn = connect()
    try:
        gate = conn.execute("SELECT * FROM day_gates WHERE day=?", (day,)).fetchone()
        loads = occupancy.store_loads(conn)
        total = sum(l["占用"] for l in loads)
        watermark = occupancy.count_in_flight(conn)
        return {
            "day": day,
            "sealed": gate["sealed"] if gate else 0,
            "状态": "已结案" if gate and gate["sealed"] else "开放",
            "在途水位": watermark,
            "占用合计": total,
            "水位一致": watermark == total,
            "超额门店": [l["code"] for l in loads if l["超额"] > 0],
            "stores": loads,
        }
    finally:
        conn.close()


@router.post("/日闸/结案")
def do_seal(body: SealIn):
    row = seal_day(body.day or None, body.note)
    row["状态"] = "已结案" if row["sealed"] else "开放"
    return row
