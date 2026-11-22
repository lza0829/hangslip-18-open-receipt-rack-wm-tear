"""占用口径不变量测试（架构：额定满则拒）。

覆盖：
- 开票回执 / 挂牌表 / 衣物件 / 水位 / 衣架页 / 日闸预检 / 总览 同一份占用事实；
- 满格整笔拒：挂牌、衣物件、工序事件、水位增量都不落账，失败票不进任何读路径；
- 并发抢最后一格：恰有一人成功，不得双破硬闸；
- 取衣 / 丢失隔离释放格位，超期隔离仍占格；
- 水位可由写入侧谓词重放。
"""

from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.db as db
from app.db import init_db


def _payload(ticket_no: str, store: str = "S1") -> dict:
    return {
        "ticket_no": ticket_no,
        "store_code": store,
        "pickup_code": "暗码一",
        "customer_name": "测试顾客",
        "due_date": "",
        "note": "",
        "operator": "测试员",
        "items": [{"garment": "衬衣", "qty": 2, "remark": ""}],
    }


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    init_db()
    conn = db.connect()
    try:
        conn.executemany(
            "INSERT INTO stores(code, name, rack_count, active) VALUES (?,?,?,1)",
            [("S1", "一店", 2), ("S2", "二店", 1)],
        )
        conn.commit()
    finally:
        conn.close()
    return tmp_path / "test.db"


@pytest.fixture()
def client(fresh_db):
    from app.main import app  # seed_if_empty 见已有门店，直接返回

    return TestClient(app)


def _scalar(sql: str, args=()):
    conn = db.connect()
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def _watermark() -> int:
    return _scalar("SELECT value FROM watermarks WHERE key='在途票'")


def _live() -> int:
    from app.services.occupancy import OCCUPANCY_WHERE

    return _scalar(f"SELECT COUNT(*) FROM hang_tickets WHERE {OCCUPANCY_WHERE}")


def _store(loads, code):
    return next(s for s in loads if s["code"] == code)


# ---------------------------------------------------------------- 口径一致


def test_receipt_and_all_read_paths_agree(client):
    r = client.post("/api/挂牌", json=_payload("T-1"))
    assert r.status_code == 200
    assert r.json()["门店占用"] == {
        "store_code": "S1",
        "额定": 2,
        "占用": 1,
        "剩余": 1,
    }

    stores = client.get("/api/门店").json()
    s1 = _store(stores, "S1")
    assert (s1["占用"], s1["额定"], s1["剩余"], s1["已满"]) == (1, 2, 1, False)

    pre = client.get("/api/日闸/预检").json()
    assert pre["在途水位"] == 1
    assert pre["占用合计"] == 1
    assert pre["水位一致"] is True
    assert pre["超额门店"] == []
    assert _store(pre["stores"], "S1")["占用"] == 1

    dash = client.get("/api/总览").json()
    assert dash["在途水位"] == 1
    assert _store(dash["门店占用"], "S1")["占用"] == 1

    # 落账水位与谓词重放一致
    assert _watermark() == 1 == _live()

    detail = client.get("/api/挂牌/T-1").json()
    assert len(detail["items"]) == 1
    assert detail["items"][0]["qty"] == 2


# ---------------------------------------------------------------- 满格整笔拒


def test_full_store_rejects_and_rolls_back(client):
    assert client.post("/api/挂牌", json=_payload("T-1", "S2")).status_code == 200
    r = client.post("/api/挂牌", json=_payload("T-2", "S2"))
    assert r.status_code == 409
    assert "已满" in r.json()["detail"]

    # 整笔回到开票前：挂牌、衣物件、工序事件、水位都不留痕迹
    assert _scalar("SELECT COUNT(*) FROM hang_tickets") == 1
    assert _scalar("SELECT COUNT(*) FROM ticket_items") == 1
    assert _scalar("SELECT COUNT(*) FROM process_events") == 1
    assert _watermark() == 1 == _live()

    # 失败票不混入任何占用读路径
    nos = [t["ticket_no"] for t in client.get("/api/挂牌").json()]
    assert nos == ["T-1"]
    s2 = _store(client.get("/api/门店").json(), "S2")
    assert (s2["占用"], s2["额定"], s2["已满"]) == (1, 1, True)
    assert client.get("/api/挂牌/T-2").status_code == 404

    # 取衣核销释放格位后可再开票
    client.post("/api/挂牌/T-1/推进", json={"to_status": "待取"})
    client.post("/api/挂牌/T-1/取衣", json={"pickup_code": "暗码一"})
    assert _store(client.get("/api/门店").json(), "S2")["占用"] == 0
    assert client.post("/api/挂牌", json=_payload("T-3", "S2")).status_code == 200


def test_reject_is_atomic_when_items_invalid(client):
    # 第二件衣物件非法：整笔（含挂牌与第一件）都必须回滚
    body = _payload("T-9", "S2")
    body["items"] = [
        {"garment": "衬衣", "qty": 1, "remark": ""},
        {"garment": "", "qty": 1, "remark": ""},
    ]
    r = client.post("/api/挂牌", json=body)
    assert r.status_code == 400
    assert _scalar("SELECT COUNT(*) FROM hang_tickets") == 0
    assert _scalar("SELECT COUNT(*) FROM ticket_items") == 0
    assert _scalar("SELECT COUNT(*) FROM process_events") == 0
    assert _live() == 0


# ---------------------------------------------------------------- 并发抢格


def test_concurrent_last_slot_single_winner(fresh_db):
    from app.services import tickets as svc

    results = []

    def worker(i: int):
        try:
            svc.create_ticket(_payload(f"T-{i}", "S2"))
            results.append(("ok", i))
        except HTTPException as e:
            results.append((e.status_code, i))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 额定 1 格、4 人并发：恰有一人贴上，其余整笔 409，不得双破硬闸
    oks = [r for r in results if r[0] == "ok"]
    rejects = [r for r in results if r[0] == 409]
    assert len(oks) == 1 and len(rejects) == 3
    assert _scalar("SELECT COUNT(*) FROM hang_tickets WHERE store_code='S2'") == 1
    assert _scalar("SELECT COUNT(*) FROM ticket_items") == 1
    assert _live() == 1
    assert _watermark() == 1


# ---------------------------------------------------------------- 状态迁移与格位


def test_overdue_quarantine_still_occupies_lost_frees(client):
    assert client.post("/api/挂牌", json=_payload("T-1", "S2")).status_code == 200
    client.post("/api/挂牌/T-1/推进", json={"to_status": "待取"})

    # 超期隔离仍占格：新店票仍被硬闸拒
    client.post("/api/挂牌/T-1/隔离", json={"kind": "超期隔离", "reason": "逾承诺日"})
    assert _store(client.get("/api/门店").json(), "S2")["占用"] == 1
    assert client.post("/api/挂牌", json=_payload("T-2", "S2")).status_code == 409

    # 丢失隔离释放格位
    client.post("/api/挂牌/T-1/隔离", json={"kind": "丢失隔离", "reason": "清点缺失"})
    assert _store(client.get("/api/门店").json(), "S2")["占用"] == 0
    assert client.post("/api/挂牌", json=_payload("T-2", "S2")).status_code == 200


def test_watermark_replayable_after_each_mutation(client):
    client.post("/api/挂牌", json=_payload("T-1"))
    client.post("/api/挂牌", json=_payload("T-2"))
    assert _watermark() == _live() == 2

    client.post("/api/挂牌/T-1/推进", json={"to_status": "水洗"})
    assert _watermark() == _live() == 2

    client.post("/api/挂牌/T-1/推进", json={"to_status": "待取"})
    client.post("/api/挂牌/T-1/取衣", json={"pickup_code": "暗码一"})
    assert _watermark() == _live() == 1
    assert client.get("/api/总览").json()["在途水位"] == 1


# ---------------------------------------------------------------- 日闸


def test_sealed_day_blocks_create(client):
    client.post("/api/日闸/结案", json={"note": "打烊"})
    r = client.post("/api/挂牌", json=_payload("T-1"))
    assert r.status_code == 409
    assert _scalar("SELECT COUNT(*) FROM hang_tickets") == 0
    assert client.get("/api/日闸/预检").json()["状态"] == "已结案"
