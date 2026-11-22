import { useEffect, useState } from "react";
import { api } from "../api";

export default function Intake() {
  const [stores, setStores] = useState([]);
  const [msg, setMsg] = useState("");
  const [ok, setOk] = useState(false);
  const [form, setForm] = useState({
    ticket_no: "",
    store_code: "",
    pickup_code: "",
    customer_name: "",
    due_date: "",
    note: "",
    garment: "衬衣",
    qty: 1,
  });

  async function loadStores() {
    const rows = await api.门店();
    const active = rows.filter((s) => s.active);
    setStores(active);
    setForm((f) =>
      f.store_code && active.some((s) => s.code === f.store_code)
        ? f
        : { ...f, store_code: active[0]?.code || "" }
    );
  }

  useEffect(() => {
    loadStores().catch((e) => setMsg(e.message));
  }, []);

  const cur = stores.find((s) => s.code === form.store_code);

  async function submit(e) {
    e.preventDefault();
    setMsg("");
    setOk(false);
    try {
      const row = await api.开票({
        ticket_no: form.ticket_no,
        store_code: form.store_code,
        pickup_code: form.pickup_code,
        customer_name: form.customer_name,
        due_date: form.due_date,
        note: form.note,
        items: [{ garment: form.garment, qty: Number(form.qty), remark: "" }],
      });
      const occ = row.门店占用;
      setOk(true);
      setMsg(
        `已开票 ${row.ticket_no}，状态 ${row.status}；本店占用 ${occ.占用}/${occ.额定}，余 ${occ.剩余}`
      );
      setForm((f) => ({ ...f, ticket_no: "", pickup_code: "", customer_name: "", note: "" }));
    } catch (err) {
      setMsg(err.message);
    }
    // 无论成败都重拉占用，保证入口显示的格子数与写入侧谓词一致
    loadStores().catch(() => {});
  }

  return (
    <section className="panel">
      <h3>收衣开票</h3>
      <p className={ok ? "msg ok" : "msg"}>{msg}</p>
      {cur && (
        <p className="msg">
          当前门店「{cur.name}」占用 {cur.占用}/额定 {cur.额定}，剩余 {cur.剩余} 格
          {cur.已满 && <span className="badge 已满">已满，开票将被整笔拒绝</span>}
        </p>
      )}
      <form onSubmit={submit}>
        <div className="row">
          <input
            placeholder="票号"
            value={form.ticket_no}
            onChange={(e) => setForm({ ...form, ticket_no: e.target.value })}
            required
          />
          <select
            value={form.store_code}
            onChange={(e) => setForm({ ...form, store_code: e.target.value })}
          >
            {stores.map((s) => (
              <option key={s.code} value={s.code}>
                {s.name}（{s.占用}/{s.额定}）
              </option>
            ))}
          </select>
          <input
            placeholder="取衣暗码"
            value={form.pickup_code}
            onChange={(e) => setForm({ ...form, pickup_code: e.target.value })}
            required
          />
          <input
            placeholder="顾客称呼"
            value={form.customer_name}
            onChange={(e) => setForm({ ...form, customer_name: e.target.value })}
            required
          />
        </div>
        <div className="row">
          <input
            type="date"
            value={form.due_date}
            onChange={(e) => setForm({ ...form, due_date: e.target.value })}
          />
          <input
            placeholder="衣物"
            value={form.garment}
            onChange={(e) => setForm({ ...form, garment: e.target.value })}
          />
          <input
            type="number"
            min="1"
            value={form.qty}
            onChange={(e) => setForm({ ...form, qty: e.target.value })}
          />
          <input
            placeholder="备注"
            value={form.note}
            onChange={(e) => setForm({ ...form, note: e.target.value })}
          />
          <button type="submit" disabled={!cur || cur.已满}>
            开票贴号
          </button>
        </div>
      </form>
    </section>
  );
}
