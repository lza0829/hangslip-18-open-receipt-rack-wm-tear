from __future__ import annotations

from fastapi import APIRouter

from app.db import connect
from app.services import occupancy
from app.services.gate import today_str

router = APIRouter(tags=["总览"])


def _watermark_pair(conn) -> tuple[int, int]:
    """（在途重放， 落账审计）：两者同数才算水位一致。"""
    return occupancy.count_in_flight(conn), occupancy.read_watermark(conn)


@router.get("/总览")
def dashboard():
    conn = connect()
    try:
        def cnt(status: str) -> int:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM hang_tickets WHERE status=?", (status,)
            ).fetchone()["c"]

        day = today_str()
        gate = conn.execute("SELECT * FROM day_gates WHERE day=?", (day,)).fetchone()
        # 在途水位：与开票判定、衣架页、日闸预检同一占用谓词实时重放；
        # 落账水位为审计值，两者对账结果一并给出，差异直接亮在总览上。
        live, persisted = _watermark_pair(conn)
        loads = occupancy.store_loads(conn)
        return {
            "今日": day,
            "日闸": "已结案" if gate and gate["sealed"] else "开放",
            "在途水位": live,
            "落账水位": persisted,
            "水位一致": live == persisted == sum(l["占用"] for l in loads),
            "门店占用": loads,
            "已收": cnt("已收"),
            "水洗": cnt("水洗"),
            "烘干": cnt("烘干"),
            "熨烫": cnt("熨烫"),
            "待取": cnt("待取"),
            "已取": cnt("已取"),
            "超期隔离": cnt("超期隔离"),
            "丢失隔离": cnt("丢失隔离"),
        }
    finally:
        conn.close()


@router.get("/水位")
def watermark():
    """水位接口：落账审计值与占用谓词重放对账，差异不藏。"""
    conn = connect()
    try:
        live, persisted = _watermark_pair(conn)
        row = conn.execute(
            "SELECT updated_at FROM watermarks WHERE key='在途票'"
        ).fetchone()
        return {
            "在途水位": live,
            "落账水位": persisted,
            "水位一致": live == persisted,
            "updated_at": row["updated_at"] if row else None,
        }
    finally:
        conn.close()
