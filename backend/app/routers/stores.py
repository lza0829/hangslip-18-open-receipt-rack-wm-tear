from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import connect
from app.services import occupancy

router = APIRouter(tags=["门店"])


class StoreIn(BaseModel):
    code: str
    name: str
    rack_count: int = Field(ge=1, default=2)


@router.get("/门店")
def list_stores():
    """门店衣架：每店附带同一占用谓词重放出的 额定/占用/剩余/已满。"""
    conn = connect()
    try:
        return occupancy.store_loads(conn)
    finally:
        conn.close()


@router.post("/门店")
def create_store(body: StoreIn):
    conn = connect()
    try:
        try:
            conn.execute(
                "INSERT INTO stores(code, name, rack_count, active) VALUES (?,?,?,1)",
                (body.code.strip(), body.name.strip(), body.rack_count),
            )
            conn.commit()
        except Exception as e:
            raise HTTPException(409, f"门店写入失败：{e}") from e
        return occupancy.store_load(conn, body.code.strip())
    finally:
        conn.close()
