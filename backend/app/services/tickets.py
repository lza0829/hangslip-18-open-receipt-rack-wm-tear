from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

from app.db import FORWARD, connect, transaction
from app.services import occupancy
from app.services.gate import ensure_day_row, is_day_sealed, today_str


def _read_ticket(conn, ticket_no: str) -> dict | None:
    """从已落行读回一张挂牌（含衣物件与工序事件）；不存在返回 None。"""
    row = conn.execute(
        "SELECT * FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["items"] = [
        dict(i)
        for i in conn.execute(
            "SELECT * FROM ticket_items WHERE ticket_no=? ORDER BY id",
            (ticket_no,),
        ).fetchall()
    ]
    data["events"] = [
        dict(e)
        for e in conn.execute(
            "SELECT * FROM process_events WHERE ticket_no=? ORDER BY id",
            (ticket_no,),
        ).fetchall()
    ]
    return data


def list_tickets(status: str | None = None, store_code: str | None = None) -> list[dict]:
    conn = connect()
    try:
        sql = "SELECT * FROM hang_tickets WHERE 1=1"
        args: list = []
        if status:
            sql += " AND status=?"
            args.append(status)
        if store_code:
            sql += " AND store_code=?"
            args.append(store_code)
        sql += " ORDER BY id DESC"
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        for r in rows:
            r["items"] = [
                dict(i)
                for i in conn.execute(
                    "SELECT * FROM ticket_items WHERE ticket_no=? ORDER BY id",
                    (r["ticket_no"],),
                ).fetchall()
            ]
        return rows
    finally:
        conn.close()


def get_ticket(ticket_no: str) -> dict:
    conn = connect()
    try:
        data = _read_ticket(conn, ticket_no)
        if data is None:
            raise HTTPException(404, "挂牌不存在")
        return data
    finally:
        conn.close()


def _missing_legs(conn, ticket_no: str, expect_items: int) -> list[str]:
    """回执证据链核验：挂牌行、衣物件、工序事件、占用重算、落账水位五腿齐才算开票成立。

    在写事务内、提交前调用；返回缺腿名列表，空列表表示证据链完整。
    任一缺腿 → 调用方抛错，整笔回滚：回执作废，水位/衣架占用增量一并回退。
    """
    missing: list[str] = []
    t = conn.execute(
        "SELECT status FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
    ).fetchone()
    if not t:
        return ["挂牌行"]
    n_items = conn.execute(
        "SELECT COUNT(*) AS c FROM ticket_items WHERE ticket_no=?", (ticket_no,)
    ).fetchone()["c"]
    if n_items == 0 or n_items != expect_items:
        missing.append("衣物件")
    n_events = conn.execute(
        "SELECT COUNT(*) AS c FROM process_events WHERE ticket_no=?", (ticket_no,)
    ).fetchone()["c"]
    if n_events == 0:
        missing.append("工序事件")
    # 占格态的票必须能被占用谓词重算到（衣架页/预检/总览同源的那一份）。
    if t["status"] not in occupancy.VACATING_STATUSES:
        counted = conn.execute(
            f"SELECT COUNT(*) AS c FROM hang_tickets "
            f"WHERE ticket_no=? AND {occupancy.OCCUPANCY_WHERE}",
            (ticket_no,),
        ).fetchone()["c"]
        if counted != 1:
            missing.append("占用重算")
    # 落账水位必须与占用谓词重放同数（同事务内 sync_watermark 之后必然成立）。
    if occupancy.read_watermark(conn) != occupancy.count_in_flight(conn):
        missing.append("落账水位")
    return missing


def create_ticket(payload: dict) -> dict:
    """开票贴号（架构：以挂牌行加衣物件加占用重算为准，额定满则拒）。

    判定占用、写挂牌、写衣物件、写工序事件、水位重算在同一事务内同成败；
    提交前做回执收口核验——票号必须能由已落行与占用重算算出来，
    缺行缺件则整笔回滚：回执作废，水位与衣架占用增量一并回退。
    回执本体只从已落行读回，绝不凭入参拼装。
    """
    if is_day_sealed():
        raise HTTPException(409, "今日已结案，禁止新开挂牌")
    ticket_no = (payload.get("ticket_no") or "").strip()
    store_code = (payload.get("store_code") or "").strip()
    pickup_code = (payload.get("pickup_code") or "").strip()
    customer_name = (payload.get("customer_name") or "").strip()
    due_date = (payload.get("due_date") or "").strip() or today_str()
    note = (payload.get("note") or "").strip()
    items = payload.get("items") or []
    if not ticket_no or not store_code or not pickup_code or not customer_name:
        raise HTTPException(400, "票号、门店、暗码、顾客名必填")
    if not items:
        raise HTTPException(400, "至少一件衣物")

    now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    with transaction() as conn:
        # BEGIN IMMEDIATE：本事务持有写锁，判定占用与写入挂牌被串行化，
        # 并发抢最后一格不会双破硬闸；任一步失败整笔回滚到开票前。
        ensure_day_row(conn)
        if is_day_sealed(conn=conn):
            raise HTTPException(409, "今日已结案，禁止新开挂牌")
        store = conn.execute(
            "SELECT * FROM stores WHERE code=? AND active=1", (store_code,)
        ).fetchone()
        if not store:
            raise HTTPException(400, "门店不可用")
        exists = conn.execute(
            "SELECT 1 FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if exists:
            raise HTTPException(409, "票号已存在")
        # 硬闸：额定满则整笔拒。判定用与所有读路径相同的占用谓词。
        load = occupancy.store_load(conn, store_code)
        if load["已满"]:
            raise HTTPException(
                409,
                f"门店「{store['name']}」衣架已满：占用{load['占用']}/额定{load['额定']}，"
                "整笔开票未落账",
            )
        conn.execute(
            """INSERT INTO hang_tickets
               (ticket_no, store_code, pickup_code, customer_name, status, opened_at, due_date, note)
               VALUES (?,?,?,?, '已收',?,?,?)""",
            (ticket_no, store_code, pickup_code, customer_name, now, due_date, note),
        )
        for it in items:
            garment = (it.get("garment") or "").strip()
            qty = int(it.get("qty") or 0)
            if not garment or qty <= 0:
                raise HTTPException(400, "衣物件不合法")
            conn.execute(
                "INSERT INTO ticket_items(ticket_no, garment, qty, remark) VALUES (?,?,?,?)",
                (ticket_no, garment, qty, (it.get("remark") or "").strip()),
            )
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (ticket_no, None, "已收", payload.get("operator") or "店员", now, "收衣开票"),
        )
        # 水位与挂牌同事务重算落账：要么一起成，要么一起回退。
        watermark = occupancy.sync_watermark(conn)
        # 回执收口核验：缺任何一腿即抛错，transaction() 整笔回滚，
        # 回执作废——不允许「票号已回、占格证据缺失」的半截开票出门。
        legs = _missing_legs(conn, ticket_no, expect_items=len(items))
        if legs:
            raise HTTPException(
                500,
                f"开票证据链缺腿（{'、'.join(legs)}），回执作废，水位与占用增量已整体回退",
            )
        # 回执只从已落行读回；占用与判定、水位同一谓词，同事务内重放。
        receipt = _read_ticket(conn, ticket_no)
        receipt_load = occupancy.store_load(conn, store_code)
    receipt["门店占用"] = {
        "store_code": store_code,
        "额定": receipt_load["额定"],
        "占用": receipt_load["占用"],
        "剩余": receipt_load["剩余"],
    }
    receipt["回执核验"] = {
        "挂牌行": True,
        "衣物件": len(receipt["items"]),
        "占用重算": receipt_load["占用"],
        "在途水位": watermark,
    }
    return receipt


def advance(ticket_no: str, to_status: str, operator: str = "店员", note: str = "") -> dict:
    if is_day_sealed():
        raise HTTPException(409, "今日已结案，禁止改工序")
    to_status = (to_status or "").strip()
    with transaction() as conn:
        ensure_day_row(conn)
        row = conn.execute(
            "SELECT * FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "挂牌不存在")
        cur = row["status"]
        if cur in ("已取", "超期隔离", "丢失隔离"):
            raise HTTPException(409, f"当前态「{cur}」不可推进工序")
        allowed = FORWARD.get(cur, ())
        if to_status not in allowed:
            raise HTTPException(409, f"不可从「{cur}」到「{to_status}」")
        now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        conn.execute(
            "UPDATE hang_tickets SET status=? WHERE ticket_no=?",
            (to_status, ticket_no),
        )
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (ticket_no, cur, to_status, operator or "店员", now, note or ""),
        )
        occupancy.sync_watermark(conn)
    return get_ticket(ticket_no)


def pickup(ticket_no: str, pickup_code: str, operator: str = "店员") -> dict:
    if is_day_sealed():
        raise HTTPException(409, "今日已结案，禁止取衣核销")
    with transaction() as conn:
        ensure_day_row(conn)
        row = conn.execute(
            "SELECT * FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "挂牌不存在")
        if row["status"] != "待取":
            raise HTTPException(409, f"仅「待取」可核销，当前「{row['status']}」")
        if (pickup_code or "").strip() != row["pickup_code"]:
            raise HTTPException(403, "取衣暗码不符")
        now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        conn.execute(
            "UPDATE hang_tickets SET status='已取', closed_at=? WHERE ticket_no=?",
            (now, ticket_no),
        )
        conn.execute(
            """INSERT INTO pickups(ticket_no, pickup_code, picked_at, operator)
               VALUES (?,?,?,?)""",
            (ticket_no, row["pickup_code"], now, operator or "店员"),
        )
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (ticket_no, "待取", "已取", operator or "店员", now, "取衣核销"),
        )
        occupancy.sync_watermark(conn)
    return get_ticket(ticket_no)


def quarantine(ticket_no: str, kind: str, reason: str, operator: str = "店员") -> dict:
    kind = (kind or "").strip()
    if kind not in ("超期隔离", "丢失隔离"):
        raise HTTPException(400, "隔离种类仅超期隔离或丢失隔离")
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM hang_tickets WHERE ticket_no=?", (ticket_no,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "挂牌不存在")
        if row["status"] in ("已取", "丢失隔离"):
            raise HTTPException(409, f"当前态「{row['status']}」不可隔离")
        if kind == "超期隔离" and row["status"] not in ("待取", "超期隔离"):
            raise HTTPException(409, "超期隔离仅从待取进入")
        now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
        prev = row["status"]
        conn.execute(
            "UPDATE hang_tickets SET status=? WHERE ticket_no=?",
            (kind, ticket_no),
        )
        conn.execute(
            """INSERT INTO quarantines(ticket_no, kind, reason, created_at, resolved)
               VALUES (?,?,?,?,0)""",
            (ticket_no, kind, reason or kind, now),
        )
        conn.execute(
            """INSERT INTO process_events(ticket_no, from_status, to_status, operator, occurred_at, note)
               VALUES (?,?,?,?,?,?)""",
            (ticket_no, prev, kind, operator or "店员", now, reason or ""),
        )
        occupancy.sync_watermark(conn)
    return get_ticket(ticket_no)
