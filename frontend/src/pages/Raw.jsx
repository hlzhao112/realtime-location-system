import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Modal } from "../ui";

function todayStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function placeText(row, { codes = false } = {}) {
  const line = row?.lineName || row?.lineCode || "";
  const proc = row?.procName || row?.procCode || "";
  const area = row?.areaName || row?.areaCode || "";
  if (!line && !proc && !area) return "未匹配映射";
  const main = [line, proc, area].filter(Boolean).join(" · ");
  if (!codes) return main;
  const ids = [row?.lineCode, row?.procCode, row?.areaCode].filter(Boolean).join(" / ");
  return ids ? `${main}（${ids}）` : main;
}

export default function Raw({ project, toast }) {
  const [day, setDay] = useState(todayStr());
  const [q, setQ] = useState("");
  const [auto, setAuto] = useState(false);
  const [data, setData] = useState({ devices: [], stats: { active: 0, inactive: 0, total: 0, today: 0 } });
  const [modal, setModal] = useState(null);
  const [mqtt, setMqtt] = useState(null);
  const [busy, setBusy] = useState(false);
  const pid = project.pid;

  const load = useCallback(async () => {
    const [r, m] = await Promise.all([api.raw(pid, day, q), api.mqttStatus().catch(() => null)]);
    setData(r);
    if (m) setMqtt(m);
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
            <span style={{ fontSize: 12, color: "var(--t2)" }}>
              绑定 {d.tz} · EPC {d.epc || "—"}
              {d.lineName || d.procName || d.areaName ? ` · ${placeText(d)}` : ""}
            </span>
          </div>
          <div className="tb-wrap" style={{ maxHeight: 360 }}>
            <table className="tb">
              <thead>
                <tr>
                  <th className="num">序号</th>
                  <th>时间戳</th>
                  <th>EPC</th>
                  <th>产线</th>
                  <th>工序</th>
                  <th>区域</th>
                  <th>Hex 数据</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((r) => (
                  <tr key={r.no}>
                    <td className="num">{r.no}</td>
                    <td className="mono">
                      {r.ts}
                      {r.test ? <span className="src-edited" style={{ marginLeft: 6 }}>测试</span> : null}
                    </td>
                    <td className="mono">{r.epc || "—"}</td>
                    <td title={r.lineCode || ""}>
                      {r.lineName || r.lineCode || "—"}
                      {r.lineCode && r.lineName ? <div className="src-sub mono">{r.lineCode}</div> : null}
                    </td>
                    <td title={r.procCode || ""}>
                      {r.procName || r.procCode || "—"}
                      {r.procCode && r.procName ? <div className="src-sub mono">{r.procCode}</div> : null}
                    </td>
                    <td title={r.areaCode || ""}>
                      {r.areaName || r.areaCode || "—"}
                      {r.areaCode && r.areaName ? <div className="src-sub mono">{r.areaCode}</div> : null}
                    </td>
                    <td className="mono">{r.hex}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!d.items.length ? <div className="empty">该设备暂无上报记录。</div> : null}
          </div>
          <div className="hint" style={{ marginTop: 12 }}>
            共 {d.n} 条记录 · EPC 按映射表解析产线 / 工序 / 区域；未命中映射时显示「—」。本页只读，不提供编辑 / 删除入口。
          </div>
        </Modal>
      );
    } catch (e) {
      toast(e.message);
    }
  }

  function confirmRefetch() {
    setModal(
      <Modal
        title="重新获取该项目历史数据"
        onClose={() => setModal(null)}
        foot={
          <>
            <button className="btn" onClick={() => setModal(null)}>取消</button>
            <button className="btn primary" onClick={doRefetch}>开始获取</button>
          </>
        }
      >
        <div className="alert">
          将向 MQTT 重新订阅 <b>dev/coo/+</b>，只写入本项目能匹配到的报文（PID / 项目名称 / 读卡器编号 = 报文里的 <b>dev</b>）。
          Broker 通常只保留每个主题的最后一条（retain）以及当前正在上报的数据，<b>无法保证补全全部历史流水</b>。
        </div>
      </Modal>
    );
  }

  async function doRefetch() {
    setBusy(true);
    setModal(null);
    try {
      const r = await api.rawRefetch(pid);
      await load();
      toast(
        `收到 ${r.collected || 0} 条，本项目匹配 ${r.matched || 0} 条，已写入 ${r.ingested || 0} 条`,
        "历史数据"
      );
    } catch (e) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  }

  const s = data.stats || {};
  return (
    <div>
      <div className="kicker">模块 B · 项目内部 · 只读</div>
      <h1 className="page-title">原始数据监控（RFID 设备 EPC 数据）</h1>
      <p className="sec-sub">
        数据来自 MQTT 主题 <b>dev/coo/+</b>，按报文 <b>dev</b> 字段归属本项目。仅展示各读卡设备上报的原始 HEX，并按映射表标注 EPC 对应的产线 / 工序 / 区域；只读、不可编辑或删除。
        {mqtt ? (
          <span className={"tag " + (mqtt.connected ? "ok" : mqtt.enabled ? "err" : "")} style={{ marginLeft: 8 }}>
            {mqtt.connected ? "MQTT 已连接" : mqtt.enabled ? "MQTT 未连接" : "MQTT 已关闭"}
          </span>
        ) : null}
      </p>
      <div className="toolbar">
        <input className="search" placeholder="搜索设备 / EPC / 产线 / 工序 / 区域" value={q} onChange={(e) => setQ(e.target.value)} />
        <div style={{ flex: 1 }} />
        <label className="chk">
          <input type="checkbox" style={{ width: 13, height: 13 }} checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          自动刷新
        </label>
        <input className="input" type="date" value={day} onChange={(e) => setDay(e.target.value)} style={{ width: 152, height: 32 }} />
        <button className="btn sm" onClick={() => load().then(() => toast("已拉取本项目最新数据")).catch((e) => toast(e.message))}>↻ 手动刷新</button>
        <button className="btn sm primary" disabled={busy} onClick={confirmRefetch}>
          {busy ? "正在获取…" : "重新获取该项目历史数据"}
        </button>
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
            <div className="place">{placeText(d)}</div>
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
