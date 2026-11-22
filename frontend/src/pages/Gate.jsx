import { useEffect, useState } from "react";
import { api } from "../api";

export default function Gate() {
  const [today, setToday] = useState(null);
  const [rows, setRows] = useState([]);
  const [pre, setPre] = useState(null);
  const [msg, setMsg] = useState("");

  async function load() {
    const [t, list, p] = await Promise.all([
      api.日闸今日(),
      api.日闸列表(),
      api.日闸预检(),
    ]);
    setToday(t);
    setRows(list);
    setPre(p);
  }

  useEffect(() => {
    load().catch((e) => setMsg(e.message));
  }, []);

  async function seal() {
    setMsg("");
    try {
      const row = await api.结案({ note: "营业日结案" });
      setMsg(`已结案：${row.day}`);
      await load();
    } catch (e) {
      setMsg(e.message);
    }
  }

  return (
    <section className="panel">
      <h3>结案日闸</h3>
      <p className="msg">{msg}</p>
      <div className="row">
        <div className="metric">
          <b>{today?.day || "—"}</b>
          <span>今日</span>
        </div>
        <div className="metric">
          <b>{today?.状态 || "—"}</b>
          <span>日闸状态</span>
        </div>
        <button type="button" onClick={seal} disabled={today?.sealed}>
          今日结案
        </button>
      </div>
      {pre && (
        <>
          <h4>结案预检（与开票判定、衣架页同一占用口径）</h4>
          <div className="row">
            <div className="metric">
              <b>{pre.在途水位}</b>
              <span>在途水位</span>
            </div>
            <div className="metric">
              <b>{pre.落账水位}</b>
              <span>落账水位</span>
            </div>
            <div className="metric">
              <b>{pre.占用合计}</b>
              <span>衣架占用合计</span>
            </div>
            <div className="metric">
              <b>{pre.水位一致 ? "一致" : "不一致"}</b>
              <span>水位核对</span>
            </div>
          </div>
          {!pre.水位一致 && (
            <p className="msg warn">
              三账分叉：在途重算 {pre.在途水位} / 落账 {pre.落账水位} / 衣架合计{" "}
              {pre.占用合计}，存在未同源的开票痕迹
            </p>
          )}
          {pre.超额门店.length > 0 && (
            <p className="msg">超额门店：{pre.超额门店.join("、")}</p>
          )}
          <table>
            <thead>
              <tr>
                <th>代号</th>
                <th>名称</th>
                <th>占用</th>
                <th>额定</th>
                <th>剩余</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {pre.stores.map((s) => (
                <tr key={s.code}>
                  <td>{s.code}</td>
                  <td>{s.name}</td>
                  <td>{s.占用}</td>
                  <td>{s.额定}</td>
                  <td>{s.剩余}</td>
                  <td>{s.已满 ? <span className="badge 已满">已满</span> : "可开"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <table>
        <thead>
          <tr>
            <th>日期</th>
            <th>状态</th>
            <th>结案时刻</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.day}>
              <td>{r.day}</td>
              <td>{r.状态}</td>
              <td>{r.sealed_at || "—"}</td>
              <td>{r.note || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
