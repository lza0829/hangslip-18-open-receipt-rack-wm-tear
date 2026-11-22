"""占用口径的唯一权威定义（全局只此一份）。

架构选择（全局唯一）：**以挂牌行加衣物件加占用重算为准，额定满则拒**。
- 占用谓词：挂牌状态不在 VACATING_STATUSES（已取、丢失隔离）中，即占用本店一个格位；
  一张挂牌占一格，衣物件数量不改变占格数。
- 额定格位 = stores.rack_count。
- 写入侧（开票判定）与全部读取侧（回执、水位、衣架页、日闸预检、总览）必须
  使用本模块的同一谓词，禁止各写各的口径；页面上的占用必须能由该谓词重放出来。
- 开票时「判定占用 + 写入挂牌」在同一事务内完成：满格则整笔 409 回滚，
  挂牌、衣物件、工序事件、水位增量都不落账，失败票不混入任何占用读路径。
- 假开票收口：回执票号必须能由已落挂牌行、衣物件与占用重算算出来；
  缺行或缺件则作废该回执，水位/衣架占用增量随同事务整体回退，
  不允许「票号已回、占格证据缺失」的半截开票留在任何读路径上。
- watermarks 表只是落账审计值：读路径一律用 count_in_flight 实时重放，
  审计值与重放值的对账差异由日闸预检与总览亮出（如库被绕开应用改坏）。
"""

from __future__ import annotations

from datetime import datetime

# 离架终态：不再占用格位的状态。除此之外的全部状态（含超期隔离）都占格。
VACATING_STATUSES = ("已取", "丢失隔离")

# 占用谓词（SQL 片段）：全库唯一出处，读写两侧都从这里取，不得复制改写。
OCCUPANCY_WHERE = "status NOT IN ('已取', '丢失隔离')"


def count_in_flight(conn, store_code: str | None = None) -> int:
    """按占用谓词重放在途（占格）票数；给 store_code 则限定本店。"""
    sql = f"SELECT COUNT(*) AS c FROM hang_tickets WHERE {OCCUPANCY_WHERE}"
    args: list = []
    if store_code:
        sql += " AND store_code=?"
        args.append(store_code)
    return conn.execute(sql, args).fetchone()["c"]


def store_load(conn, store_code: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM stores WHERE code=?", (store_code,)
    ).fetchone()
    if not row:
        return None
    return _load_row(conn, dict(row))


def store_loads(conn) -> list[dict]:
    rows = [
        dict(r) for r in conn.execute("SELECT * FROM stores ORDER BY id").fetchall()
    ]
    return [_load_row(conn, r) for r in rows]


def _load_row(conn, store: dict) -> dict:
    used = count_in_flight(conn, store["code"])
    cap = int(store["rack_count"])
    return {
        **store,
        "额定": cap,
        "占用": used,
        "剩余": max(cap - used, 0),
        "已满": used >= cap,
        # 额定满则拒下恒为 0；保留此字段让日闸预检能亮出异常（如库被手工改坏）。
        "超额": max(used - cap, 0),
    }


def sync_watermark(conn) -> int:
    """把「在途票」水位按占用谓词重放后落进 watermarks 表。

    必须在改变占用的事务内调用（同事务提交/回滚），保证水位与挂牌表
    永远同源：页面读路径一律用 count_in_flight 实时重放，本表为落账审计。
    """
    n = count_in_flight(conn)
    now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    conn.execute(
        """INSERT INTO watermarks(key, value, updated_at) VALUES ('在途票', ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (n, now),
    )
    return n


def read_watermark(conn) -> int:
    """落账水位（审计值）。缺失按 0 计：此时只要在途重放非 0，对账即判不一致。

    读路径展示一律用 count_in_flight 重放；本值只用于与重放值对账，
    让「挂牌已落而水位未加」这类差异在日闸预检与总览上现形。
    """
    row = conn.execute("SELECT value FROM watermarks WHERE key='在途票'").fetchone()
    return int(row["value"]) if row else 0
