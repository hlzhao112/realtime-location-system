import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, downloadAuth } from "../api";
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

function tagRows(row) {
  if (row?.tags?.length) return row.tags;
  if (row?.epc || row?.lineName || row?.lineCode || row?.procName || row?.areaName) return [row];
  return [];
}

const PAGE_SIZE = 200;

function sortedOpts(map) {
  return Array.from(map.entries()).sort((a, b) => a[1].localeCompare(b[1], "zh"));
}

function HeaderFilter({ value, onChange, options, placeholder }) {
  return (
    <select
      className={"input th-filter" + (value ? " on" : "")}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{placeholder}</option>
      {options.map(([v, label]) => (
        <option key={v} value={v}>{label}</option>
      ))}
    </select>
  );
}

function tagPlaces(tags) {
  const out = [];
  const seen = new Set();
  (tags || []).forEach((t) => {
    const key = [t.pn, t.pc, t.line, t.lineName, t.area, t.areaName].join("|");
    if (seen.has(key)) return;
    seen.add(key);
    out.push(t);
  });
  return out;
}

function rowMatches(row, colF) {
  if (colF.tz && row.tz !== colF.tz) return false;
  const tags = row.tags?.length ? row.tags : [{}];
  return tags.some((t) =>
    (!colF.pc || t.pc === colF.pc)
    && (!colF.line || t.line === colF.line)
    && (!colF.area || t.area === colF.area)
  );
}

function visiblePlaces(row, colF) {
  return tagPlaces(
    (row.tags || []).filter((t) =>
      (!colF.pc || t.pc === colF.pc)
      && (!colF.line || t.line === colF.line)
      && (!colF.area || t.area === colF.area)
    )
  );
}

function placeMapped(t) {
  return Boolean(t?.pc || t?.pn || t?.line || t?.area || String(t?.areaName || "").trim());
}

function shownPlaces(row, colF, hideUnmatched) {
  const places = visiblePlaces(row, colF);
  return hideUnmatched ? places.filter(placeMapped) : places;
}

function placeKey(row, colF, hideUnmatched) {
  const loc = shownPlaces(row, colF, hideUnmatched).map((t) => [t.pc, t.line, t.area].join(":")).join("||");
  return `${row.tz}|${loc}`;
}

function mergeConsecutive(rows, colF, hideUnmatched) {
  const out = [];
  let prev = null;
  rows.forEach((row) => {
    const key = placeKey(row, colF, hideUnmatched);
    if (key === prev) return;
    prev = key;
    out.push(row);
  });
  return out;
}

function TagLines({ row }) {
  const tags = tagRows(row);
  if (!tags.length) return <div className="place">未匹配映射</div>;
  return (
    <div className="tags">
      {tags.map((t, i) => (
        <div className="tagline" key={`${t.epc || "tag"}-${i}`}>
          {t.tagNo ? <span className="tagno mono">{t.tagNo}</span> : null}
          {t.epc ? <span className="epc epc-plain">{t.epc}</span> : null}
          <span>{placeText(t)}</span>
        </div>
      ))}
    </div>
  );
}

export default function Raw({ project, toast }) {
  const [day, setDay] = useState(todayStr());
  const [recFrom, setRecFrom] = useState(todayStr());
  const [recTo, setRecTo] = useState(todayStr());
  const [pickDay, setPickDay] = useState("");
  const [loadedRange, setLoadedRange] = useState("");
  const loadSeq = useRef(0);
  const appliedRange = useRef("");
  const [q, setQ] = useState("");
  const [auto, setAuto] = useState(false);
  const [data, setData] = useState({ devices: [], stats: { active: 0, inactive: 0, total: 0, today: 0 } });
  const [modal, setModal] = useState(null);
  const [mqtt, setMqtt] = useState(null);
  const [busy, setBusy] = useState(false);
  const [merge, setMerge] = useState(false);
  const [hideUnmatched, setHideUnmatched] = useState(false);
  const [colF, setColF] = useState({ tz: "", line: "", area: "", pc: "" });
  const [page, setPage] = useState(1);
  const pid = project.pid;

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    const rangeKey = `${recFrom}|${recTo}`;
    const [r, m] = await Promise.all([
      api.raw(pid, day, q, { recordFrom: recFrom, recordTo: recTo }),
      api.mqttStatus().catch(() => null),
    ]);
    if (seq !== loadSeq.current) return;
    setData(r);
    setLoadedRange(rangeKey);
    if (m) setMqtt(m);
  }, [pid, day, q, recFrom, recTo]);

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
              绑定 {d.tz}
              {tagRows(d).length > 1
                ? ` · ${tagRows(d).length} 个标签`
                : ` · 编号 ${d.tagNo || tagRows(d)[0]?.tagNo || "—"} · EPC ${d.epc || "—"}`}
              {tagRows(d).length <= 1 && (d.lineName || d.procName || d.areaName) ? ` · ${placeText(d)}` : ""}
            </span>
          </div>
          <div className="tb-wrap" style={{ maxHeight: 360 }}>
            <table className="tb raw-tb">
              <thead>
                <tr>
                  <th className="num">序号</th>
                  <th>时间戳</th>
                  <th>标签编号</th>
                  <th>EPC</th>
                  <th>产线</th>
                  <th>工序</th>
                  <th>区域</th>
                  <th className="hex-col">Hex 数据</th>
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
                    <td className="mono">
                      {tagRows(r).length ? tagRows(r).map((t, i) => (
                        <div key={i}>{t.tagNo || "—"}</div>
                      )) : "—"}
                    </td>
                    <td className="mono epc-plain">
                      {tagRows(r).length ? tagRows(r).map((t, i) => (
                        <div key={i}>{t.epc || "—"}</div>
                      )) : "—"}
                    </td>
                    <td>
                      {tagRows(r).length ? tagRows(r).map((t, i) => (
                        <div key={i} title={t.lineCode || ""}>
                          {t.lineName || t.lineCode || (t.procName || t.areaName ? "—" : "未匹配映射")}
                          {t.lineCode && t.lineName ? <div className="src-sub mono">{t.lineCode}</div> : null}
                        </div>
                      )) : "—"}
                    </td>
                    <td>
                      {tagRows(r).length ? tagRows(r).map((t, i) => (
                        <div key={i} title={t.procCode || ""}>
                          {t.procName || t.procCode || "—"}
                          {t.procCode && t.procName ? <div className="src-sub mono">{t.procCode}</div> : null}
                        </div>
                      )) : "—"}
                    </td>
                    <td>
                      {tagRows(r).length ? tagRows(r).map((t, i) => (
                        <div key={i} title={t.areaCode || ""}>
                          {t.areaName || t.areaCode || "—"}
                          {t.areaCode && t.areaName ? <div className="src-sub mono">{t.areaCode}</div> : null}
                        </div>
                      )) : "—"}
                    </td>
                    <td className="mono hex-col">{r.hex}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!d.items.length ? <div className="empty">该设备暂无上报记录。</div> : null}
          </div>
          <div className="hint" style={{ marginTop: 12 }}>
            共 {d.n} 条记录 · 一条 HEX 中的多个标签会全部解析，并按映射表标注标签编号与产线 / 工序 / 区域；未命中映射时显示「未匹配映射」。本页只读，不提供编辑 / 删除入口。
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

  const records = data.records || [];
  const dayCounts = useMemo(() => {
    const map = new Map();
    records.forEach((r) => {
      const d = (r.time || "").slice(0, 10);
      if (d) map.set(d, (map.get(d) || 0) + 1);
    });
    return [...map.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1));
  }, [records]);
  const filtered = useMemo(() => {
    const matched = records.filter((r) => {
      if (pickDay && !(r.time || "").startsWith(pickDay)) return false;
      if (!rowMatches(r, colF)) return false;
      if (hideUnmatched && !shownPlaces(r, colF, true).length) return false;
      return true;
    });
    return merge ? mergeConsecutive(matched, colF, hideUnmatched) : matched;
  }, [records, colF, merge, pickDay, hideUnmatched]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageRows = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const showPager = filtered.length > PAGE_SIZE;

  useEffect(() => {
    setPage(1);
  }, [day, q, pid, recFrom, recTo, pickDay]);

  useEffect(() => {
    const start = recFrom && recTo && recFrom > recTo ? recTo : recFrom;
    const end = recFrom && recTo && recFrom > recTo ? recFrom : recTo;
    const key = `${recFrom}|${recTo}`;
    if (!start || !end || start === end || loadedRange !== key || appliedRange.current === key) return;
    appliedRange.current = key;
    const days = dayCounts.map(([d]) => d);
    if (days.includes(start)) {
      setPickDay(start);
      return;
    }
    setPickDay(days.find((d) => d >= start) || days[0] || "");
  }, [loadedRange, recFrom, recTo, dayCounts]);

  function changeRange(which, value) {
    const nextFrom = which === "from" ? value : recFrom;
    const nextTo = which === "to" ? value : recTo;
    if (which === "from") setRecFrom(value);
    else setRecTo(value);
    const start = nextFrom && nextTo && nextFrom > nextTo ? nextTo : nextFrom;
    const end = nextFrom && nextTo && nextFrom > nextTo ? nextFrom : nextTo;
    setPickDay(start && end && start !== end ? start : "");
    setPage(1);
  }

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const pcOpts = useMemo(() => {
    const seen = new Map();
    (project.lines || []).forEach((l) => {
      (l.procs || []).forEach((g) => {
        if (g.code) seen.set(g.code, `${g.name || ""} ${g.code}`.trim());
      });
    });
    records.forEach((r) => (r.tags || []).forEach((t) => {
      if (t.pc && !seen.has(t.pc)) seen.set(t.pc, `${t.pn || ""} ${t.pc}`.trim());
    }));
    return sortedOpts(seen);
  }, [records, project.lines]);
  const tzOpts = useMemo(() => {
    const seen = new Map();
    records.forEach((r) => {
      if (r.tz && r.tz !== "—") seen.set(r.tz, `${r.tz} ${r.seat || ""}`.trim());
    });
    return sortedOpts(seen);
  }, [records]);
  const lineOpts = useMemo(() => {
    const seen = new Map();
    (project.lines || []).forEach((l) => {
      if (l.id) seen.set(l.id, l.name ? `${l.name} ${l.id}` : l.id);
    });
    records.forEach((r) => (r.tags || []).forEach((t) => {
      if (t.line && !seen.has(t.line)) seen.set(t.line, t.lineName ? `${t.lineName} ${t.line}` : t.line);
    }));
    return sortedOpts(seen);
  }, [records, project.lines]);
  const areaOpts = useMemo(() => {
    const seen = new Map();
    (project.lines || []).forEach((l) => {
      (l.procs || []).forEach((g) => {
        (g.areas || []).forEach((a) => {
          if (a?.id) seen.set(a.id, String(a.name || "").trim() || a.id);
        });
      });
    });
    records.forEach((r) => (r.tags || []).forEach((t) => {
      if (t.area && !seen.has(t.area)) seen.set(t.area, String(t.areaName || "").trim() || t.area);
    }));
    return sortedOpts(seen);
  }, [records, project.lines]);

  function applyColF(key, value) {
    setColF((f) => ({ ...f, [key]: value }));
    setPage(1);
  }

  const s = data.stats || {};
  return (
    <div>
      <div className="kicker">模块 B · 项目内部 · 只读</div>
      <h1 className="page-title">原始数据监控（RFID 设备 EPC 数据）</h1>
      <p className="sec-sub">
        数据来自 MQTT 主题 <b>dev/coo/+</b>，按报文 <b>dev</b> 字段归属本项目。仅展示各读卡设备上报的原始 HEX，并按映射表标注标签编号与产线 / 工序 / 区域；只读、不可编辑或删除。
        {mqtt ? (
          <span className={"tag " + (mqtt.connected ? "ok" : mqtt.enabled ? "err" : "")} style={{ marginLeft: 8 }}>
            {mqtt.connected ? "MQTT 已连接" : mqtt.enabled ? "MQTT 未连接" : "MQTT 已关闭"}
          </span>
        ) : null}
      </p>
      <div className="toolbar">
        <input className="search" placeholder="搜索设备 / 标签编号 / EPC / 产线 / 工序 / 区域" value={q} onChange={(e) => setQ(e.target.value)} />
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
            <TagLines row={d} />
            <div className="foot">
              <span>{d.tz} · {d.seat}</span>
              <span>{d.n} 条 · {d.last && d.last !== "—" ? d.last.slice(11, 16) : "—"}</span>
            </div>
          </div>
        ))}
        {!(data.devices || []).length ? <div className="empty">无匹配设备。</div> : null}
      </div>

      <div className="mt24">
        <div className="sec-h">台座原始数据</div>
        <p className="sec-t">各台座读卡器收到的原始上报，按识别时间倒序。日期范围只筛选这张表，上方 EPC 看板不受影响。跨多天时先显示开始那天，可再切换其他日期。</p>
      </div>
      <div className="toolbar">
        <label className="mini-note" style={{ margin: 0 }}>开始</label>
        <input className="input" type="date" value={recFrom} onChange={(e) => changeRange("from", e.target.value)} style={{ width: 152, height: 32 }} />
        <label className="mini-note" style={{ margin: 0 }}>结束</label>
        <input className="input" type="date" value={recTo} onChange={(e) => changeRange("to", e.target.value)} style={{ width: 152, height: 32 }} />
        <button className="btn sm" onClick={() => { setMerge((m) => !m); setPage(1); toast(merge ? "已关闭合并，展示全部记录" : "已启用合并：同台车同位置连续相同数据合并为一条"); }}>{merge ? "✓ 合并连续重复" : "合并连续重复"}</button>
        <button className="btn sm" onClick={() => { setHideUnmatched((v) => !v); setPage(1); toast(hideUnmatched ? "已显示未匹配映射数据" : "已隐藏未匹配映射数据"); }}>{hideUnmatched ? "✓ 已隐藏未匹配映射" : "过滤未匹配映射"}</button>
        <button className="btn sm" onClick={() => { setColF({ tz: "", line: "", area: "", pc: "" }); setPage(1); }}>重置</button>
        <button
          className="btn sm"
          onClick={() =>
            downloadAuth(
              api.exportRawUrl(pid, {
                q,
                recordFrom: recFrom,
                recordTo: recTo,
                day: pickDay,
                tz: colF.tz,
                line: colF.line,
                area: colF.area,
                pc: colF.pc,
                merge,
                hideUnmatched,
              }),
              `${pid}-raw-records.xlsx`
            )
              .then(() => toast("已导出当前筛选结果 raw-records.xlsx"))
              .catch((e) => toast(e.message))
          }
        >
          ⤒ 导出 Excel
        </button>
        <div style={{ flex: 1 }} />
        <span className="mini-note" style={{ margin: 0 }}>{pickDay ? `${pickDay} · ` : ""}共 {filtered.length} 条</span>
      </div>
      {dayCounts.length > 1 ? (
        <div className="toolbar">
          <button className={"btn sm" + (pickDay ? "" : " primary")} onClick={() => { setPickDay(""); setPage(1); }}>全部 {records.length}</button>
          {dayCounts.map(([d, n]) => (
            <button key={d} className={"btn sm" + (pickDay === d ? " primary" : "")} onClick={() => { setPickDay(d); setPage(1); }}>
              {d.slice(5)} · {n}
            </button>
          ))}
        </div>
      ) : null}
      <div className="card pad0">
        <div className="tb-wrap" style={{ maxHeight: 840 }}>
          <table className="tb raw-tb">
            <thead>
              <tr>
                <th className="filterable">
                  台车
                  <HeaderFilter value={colF.tz} onChange={(v) => applyColF("tz", v)} options={tzOpts} placeholder="全部台车" />
                </th>
                <th className="num">序号</th>
                <th className="filterable">
                  识别工序
                  <HeaderFilter value={colF.pc} onChange={(v) => applyColF("pc", v)} options={pcOpts} placeholder="全部工序" />
                </th>
                <th className="filterable">
                  产品线
                  <HeaderFilter value={colF.line} onChange={(v) => applyColF("line", v)} options={lineOpts} placeholder="全部产品线" />
                </th>
                <th className="filterable">
                  区域
                  <HeaderFilter value={colF.area} onChange={(v) => applyColF("area", v)} options={areaOpts} placeholder="全部区域" />
                </th>
                <th>识别时间</th>
                <th className="hex-col">Hex</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((r, i) => {
                const places = shownPlaces(r, colF, hideUnmatched);
                const lines = places.length ? places : [{}];
                return (
                  <tr key={r.id}>
                    <td className="mono">{r.tz} <span style={{ fontFamily: "var(--sans)", color: "var(--t2)" }}>{r.seat}</span></td>
                    <td className="num">{(page - 1) * PAGE_SIZE + i + 1}</td>
                    <td>
                      {lines.map((t, j) => (
                        <div key={j}>
                          {t.pn || t.pc ? <>{t.pn || "—"} <span className="mono" style={{ color: "var(--t3)" }}>{t.pc}</span></> : "未匹配映射"}
                        </div>
                      ))}
                    </td>
                    <td className="mono">
                      {lines.map((t, j) => (
                        <div key={j}>{t.line || "未匹配映射"}</div>
                      ))}
                    </td>
                    <td>
                      {lines.map((t, j) => (
                        <div key={j}>{String(t.areaName || "").trim() || (t.area ? t.area : "未匹配映射")}</div>
                      ))}
                    </td>
                    <td className="mono">
                      {r.time}
                      {r.test ? <span className="src-edited" style={{ marginLeft: 6 }}>测试</span> : null}
                    </td>
                    <td className="mono hex-col">{r.hex || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!filtered.length ? <div className="empty">无匹配原始数据。</div> : null}
        </div>
        {showPager ? (
          <div className="pager">
            <button className="btn sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>上一页</button>
            <span>第 {page} / {pageCount} 页 · 共 {filtered.length} 条</span>
            <button className="btn sm" disabled={page >= pageCount} onClick={() => setPage((p) => Math.min(pageCount, p + 1))}>下一页</button>
          </div>
        ) : null}
      </div>
      {modal}
    </div>
  );
}
