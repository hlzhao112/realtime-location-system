import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

export default function MqttFeed({ toast, onBack }) {
  const [pid, setPid] = useState("");
  const [data, setData] = useState({ items: [], pids: [], total: 0 });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const r = await api.mqttRaw(pid, 1000);
    setData(r);
  }, [pid]);

  useEffect(() => {
    load().catch((e) => toast(e.message));
  }, [load, toast]);

  async function refresh() {
    setBusy(true);
    try {
      const r = await api.mqttRawRefresh();
      const next = await api.mqttRaw(pid, 1000);
      setData(next);
      toast(`已刷新，本次收到 ${r.collected || 0} 条`, "MQTT");
    } catch (e) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  }

  const items = data.items || [];
  const projects = data.projects || (data.pids || []).map((p) => ({ pid: p, name: p }));

  return (
    <section>
      <div className="kicker">模块 C · 首屏</div>
      <div className="row between">
        <h1 className="page-title">MQTT 全部数据</h1>
        <button className="btn sm" onClick={onBack}>返回项目看板</button>
      </div>
      <p className="sec-sub">
        展示从 MQTT 收到的全部定位报文。<b>Device ID</b> 对应报文 <b>dev</b>，<b>Hex</b> 对应报文 hex。
        <b>项目名称</b>为 Device ID 中包含的已有项目 PID（多条命中取最长）。
      </p>
      <div className="toolbar">
        <select className="input" style={{ width: 220, height: 32 }} value={pid} onChange={(e) => setPid(e.target.value)}>
          <option value="">全部项目</option>
          <option value="__none__">未匹配项目</option>
          {projects.map((p) => (
            <option key={p.pid} value={p.pid}>{p.name ? `${p.name}（${p.pid}）` : p.pid}</option>
          ))}
        </select>
        <div style={{ flex: 1 }} />
        <button className="btn sm primary" disabled={busy} onClick={refresh}>
          {busy ? "正在获取…" : "手动刷新最新原始数据"}
        </button>
      </div>
      <div className="statbar">
        <div className="stat"><div className="n">{items.length}</div><div className="l">当前展示</div></div>
      </div>
      <div className="tb-wrap" style={{ maxHeight: "calc(100vh - 280px)" }}>
        <table className="tb" style={{ tableLayout: "fixed" }}>
          <thead>
            <tr>
              <th style={{ width: 140, whiteSpace: "nowrap" }}>Device ID</th>
              <th style={{ width: 110, whiteSpace: "nowrap" }}>项目名称</th>
              <th>Hex</th>
              <th style={{ width: 210, whiteSpace: "nowrap" }}>时间</th>
            </tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id}>
                <td className="mono" style={{ whiteSpace: "nowrap" }}>{r.deviceId}</td>
                <td className="mono" style={{ whiteSpace: "nowrap" }}>{r.projectPid || "未匹配"}</td>
                <td className="mono" style={{ wordBreak: "break-all" }}>{r.hex}</td>
                <td className="mono" style={{ whiteSpace: "nowrap" }}>{r.ts || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!items.length ? <div className="empty">暂无 MQTT 原始数据。</div> : null}
      </div>
    </section>
  );
}
