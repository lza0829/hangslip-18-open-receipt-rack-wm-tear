# 衣票墙（hangslip）

收衣贴号 → 工序推进 → 取衣核销关账 → 超期/丢失隔离互证。

## 占用口径（全局唯一架构：额定满则拒）

本店开票占格全局只贯彻一套规则，权威定义在 `backend/app/services/occupancy.py`：

- **占用谓词**：挂牌状态 ∉（已取、丢失隔离）即占本店一格；一张挂牌占一格
  （衣物件数量不改变占格数；超期隔离仍占格，丢失隔离/已取释放）。
- **额定格位** = 门店 `rack_count`。
- **满格行为**：已达额定则整笔开票失败（409）——判定占用与写入挂牌在同一
  `BEGIN IMMEDIATE` 事务内，挂牌、衣物件、工序事件、水位增量都不落账，
  失败票不混入任何占用读路径；并发抢最后一格由写锁串行化，恰有一人成功。
- **同源读取**：开票回执、水位、衣架页（/门店）、日闸预检（/日闸/预检）、
  总览一律用同一谓词实时重放；`watermarks` 表在每次占用变更的事务内同步，
  仅作落账审计。
- 跨店转挂不在本口径内，另立需求。

## 本地开发

- 前端 http://127.0.0.1:6200
- API http://127.0.0.1:11200

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 11200

cd ..\frontend
npm install
npm run dev
```

后端测试（占用口径不变量）：

```powershell
cd backend
.\.venv\Scripts\python -m pytest tests/
```
