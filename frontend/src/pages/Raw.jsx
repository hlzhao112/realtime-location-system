import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Modal } from "../ui";

function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export default function Raw({ project, toast }) {
  const [day, setDay] = useState(todayStr());
  const [q, setQ] = useState("");
  const [auto, setAuto] = useState(false);
  const [data, setData] = useState({ devices: [], stats: { active: 0, inactive: 0, total: 0, today: 0 } });
  const [modal, setModal] = useState(null);
  const pid = project.pid;

  const load = useCallback(async () => {
    const r = await api.raw(pid, day, q);
    setData(r);
  }, [pid, day, q]);

  useEffect(() => {
    load().catch((e) => toast(e.message));
  }, [load, toast]);

  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => load().catch(() => {}), 5000);
    return () => clearInterval(t);
  }, [auto, load]);

  async function openDev(id) {
    try {
      const d = await api.rawDevice(pid, id, day);
      setModal(
        <Modal title={"设备 " + d.id + " · 原始上报记录"} wide onClose={() => setModal(null)}
          foot={<button className="btn" onClick={() => setModal(null)}>关闭</button>}>
          <div className="row between mb12">
            <span className={"tag " + (d.on ? "ok" : "")}>{d.on ? "活跃" : "非活跃"}</span>
            <span style={{ fontSize: 12, color: "var(--t2)" }}>绑定 {d.tz} · EPC {d.epc || "—"}</span>
          </div>
          <div className="tb-wrap" style={{ maxHeight: 330 }}>
            <table className="tb">
              <thead><tr><th className="num">序号</th><th>时间戳</th><th>Hex 数据</th></tr></thead>
              <tbody>
                {d.items.map((r) => (
                  <tr key={r.no}>
                    <td className="num">{r.no}</td>
                    <td className="mono">{r.ts}{r.test ? <span className="src-edited" style={{ marginLeft: 6 }}>测试</span> : null}</td>
                    <td className="mono">{r.hex}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!d.items.length ? <div className="empty">该设备暂无上报记录。</div> : null}
          </div>
          <div className="hint" style={{ marginTop: 12 }}>共 {d.n} 条记录 · 同一时刻可识别到多组标签数据，均完整保留；本页只读，不提供编辑 / 删除入口。</div>
        </Modal>
      );
    } catch (e) {
      toast(e.message);
    }
  }

  const s = data.stats || {};
  return (
    <div>
      <div className="kicker">模块 B · 项目内部 · 只读</div>
      <h1 className="page-title">原始数据监控（RFID 设备 EPC 数据）</h1>
      <p className="sec-sub">仅展示<b>本项目</b>各读卡设备上报的原始 HEX 记录，只读、不可编辑或删除；顶部仅保留手动刷新，时间直接按日期筛选本项目记录。</p>
      <div className="toolbar">
        <input className="search" placeholder="搜索设备 ID / EPC" value={q} onChange={(e) => setQ(e.target.value)} />
        <div style={{ flex: 1 }} />
        <label className="chk">
          <input type="checkbox" style={{ width: 13, height: 13 }} checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          自动刷新
        </label>
        <input className="input" type="date" value={day} onChange={(e) => setDay(e.target.value)} style={{ width: 152, height: 32 }} />
        <button className="btn sm" onClick={() => load().then(() => toast("已拉取本项目最新数据")).catch((e) => toast(e.message))}>↻ 手动刷新</button>
      </div>
      <div className="statbar">
        <div className="stat"><div className="n ok">{s.active || 0}</div><div className="l">活跃设备</div></div>
        <div className="stat"><div className="n mut">{s.inactive || 0}</div><div className="l">非活跃设备</div></div>
        <div className="stat"><div className="n">{s.total || 0}</div><div className="l">本项目总记录</div></div>
        <div className="stat"><div className="n info">{s.today || 0}</div><div className="l">本项目今日记录</div></div>
      </div>
      <div className="grid g4">
        {(data.devices || []).map((d) => (
          <div className={"dev" + (d.on ? "" : " off")} key={d.id} onClick={() => openDev(d.id)}>
            <div className="id"><span className={"led" + (d.on ? " on" : "")} />{d.id}</div>
            <div className="hex">{d.hex || "—"}</div>
            <div className="foot">
              <span>{d.tz} · {d.seat}</span>
              <span>{d.n} 条 · {d.last && d.last !== "—" ? d.last.slice(11, 16) : "—"}</span>
            </div>
          </div>
        ))}
        {!(data.devices || []).length ? <div className="empty">无匹配设备。</div> : null}
      </div>
      {modal}
    </div>
  );
}
