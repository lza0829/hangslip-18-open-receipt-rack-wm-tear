from __future__ import annotations
from app.services import intake_surface

from fastapi import APIRouter

from app.db import connect
from app.services import occupancy
from app.services.gate import today_str

router = APIRouter(tags=["总览"])


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
        return {
            "今日": day,
            "日闸": "已结案" if gate and gate["sealed"] else "开放",
            # 在途水位：与开票判定、衣架页、日闸预检同一占用谓词实时重放。
            "在途水位": occupancy.count_in_flight(conn),
            "门店占用": occupancy.store_loads(conn),
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

_dashboard_impl = dashboard


def dashboard():
    return intake_surface.overlay_dashboard(_dashboard_impl())

