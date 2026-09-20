import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, downloadAuth } from "../api";
import { Modal } from "../ui";

function srcKind(r) {
  return r.k || (r.src === "手动" ? "手动编辑" : "实时上报");
}

function uniqueOptions(rows, key, labelFn) {
  const seen = new Map();
  rows.forEach((r) => {
    const v = r[key];
    if (v == null || v === "") return;
    if (!seen.has(v)) seen.set(v, labelFn ? labelFn(r) : String(v));
  });
  return Array.from(seen.entries()).sort((a, b) => a[1].localeCompare(b[1], "zh"));
}

const PAGE_SIZE = 200;

function areaLabel(r) {
  const name = String(r?.areaName || "").trim();
  return name || "-";
}

function formatAgo(sec) {
  if (sec == null || sec === "") return "—";
  const n = Math.max(0, Math.floor(Number(sec)));
  if (!Number.isFinite(n)) return "—";
  if (n < 60) return `${n} 秒`;
  if (n < 3600) {
    const m = Math.floor(n / 60);
    const s = n % 60;
    return s ? `${m} 分 ${s} 秒` : `${m} 分`;
  }
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  return m ? `${h} 时 ${m} 分` : `${h} 时`;
}

function lastTimeShort(last) {
  const text = String(last || "").trim();
  if (!text || text === "—") return "—";
  const m = text.match(/(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : text;
}

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

function TzCard({ t, list, onDragStart }) {
  const stale = !t.online;
  const mins = t.mins == null ? "—" : t.mins;
  const areaText = String(t.areaName || "").trim() || "-";
  const tip = `最后上报 ${t.last || "—"} · 停留 ${mins} min${t.areaCode ? " · 区域 " + areaText : ""}${t.reissue ? " · 连续补发×" + t.reissue : ""} · 来源 ${t.src}`;
  return (
    <div
      className={"tz" + (stale ? " stale" : "") + (t.test ? " tmk" : "")}
      title={tip}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("text/tz", t.tz);
        e.currentTarget.setAttribute("dragging", "");
        onDragStart?.(t.tz);
      }}
      onDragEnd={(e) => e.currentTarget.removeAttribute("dragging")}
    >
      <div className="t">
        {t.tz} <span style={{ fontWeight: 400, color: "var(--t2)" }}>{t.name}</span>
      </div>
      <div className={"m" + (stale ? " warn" : "")}>
        {stale ? "停留 " : "在线 "}
        {mins} min
        {t.reissue ? ` · 补发×${t.reissue}` : ""}
        {t.src === "手动" ? " · 手动" : ""}
        {t.test ? <> · <span className="mk">测试</span></> : null}
        {list ? ` · ${t.reader}` : ""}
      </div>
      {list ? <div className="last">最近上报 {t.last || "—"}</div> : null}
    </div>
  );
}

export default function Board({ project, toast, startWizard }) {
  const [board, setBoard] = useState(null);
  const [recs, setRecs] = useState([]);
  const [qRec, setQRec] = useState("");
  const [merge, setMerge] = useState(false);
  const [colF, setColF] = useState({ tz: "", line: "", area: "", pc: "" });
  const [timeFrom, setTimeFrom] = useState("");
  const [timeTo, setTimeTo] = useState("");
  const [page, setPage] = useState(1);
  const [recTotal, setRecTotal] = useState(0);
  const [modal, setModal] = useState(null);
  const [pushingId, setPushingId] = useState(null);
  const linesRef = useRef(null);
  const [linesH, setLinesH] = useState(null);
  const inflightRef = useRef(0);
  const resizeNRef = useRef(0);
  const pid = project.pid;

  const load = useCallback(async () => {
    if (inflightRef.current) return;
    inflightRef.current = 1;
    const t0 = performance.now();
    try {
      const [b, p] = await Promise.all([api.board(pid), api.pushes(pid, qRec, merge, {
        page,
        pageSize: PAGE_SIZE,
        startTime: timeFrom || undefined,
        endTime: timeTo || undefined,
        tz: colF.tz || undefined,
        line: colF.line || undefined,
        area: colF.area || undefined,
        pc: colF.pc || undefined,
      })]);
      setBoard(b);
      setRecs(p.items || []);
      setRecTotal(typeof p.total === "number" ? p.total : (p.items || []).length);
      // #region agent log
      fetch("http://127.0.0.1:7259/ingest/42287c2f-2aca-4b5e-a241-93e737ef8f41", { method: "POST", headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "537a1e" }, body: JSON.stringify({ sessionId: "537a1e", runId: "post-fix", hypothesisId: "A,E", location: "Board.jsx:load", message: "board-load", data: { ms: Math.round(performance.now() - t0), inflight: inflightRef.current, recs: (p.items || []).length, trolleys: (b.trolleys || []).length, testOn: !!(b.test && b.test.on), testMade: b.test && b.test.made }, timestamp: Date.now() }) }).catch(() => {});
      // #endregion
    } finally {
      inflightRef.current = 0;
    }
  }, [pid, qRec, merge, page, timeFrom, timeTo, colF]);

  useEffect(() => {
    load().catch((e) => toast(e.message));
    const boardT = setInterval(() => {
      api.board(pid).then(setBoard).catch(() => {});
    }, 2000);
    const pushT = setInterval(() => load().catch(() => {}), 10000);
    // #region agent log
    const onMove = (e) => {
      const el = e.target;
      fetch("http://127.0.0.1:7259/ingest/42287c2f-2aca-4b5e-a241-93e737ef8f41", { method: "POST", headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "537a1e" }, body: JSON.stringify({ sessionId: "537a1e", runId: "post-fix", hypothesisId: "B,D", location: "Board.jsx:mousemove", message: "mouse", data: { cursor: el && el.nodeType === 1 ? getComputedStyle(el).cursor : "", cls: (el && el.className) || "", pointerLock: !!document.pointerLockElement, buttons: e.buttons }, timestamp: Date.now() }) }).catch(() => {});
    };
    window.addEventListener("mousemove", onMove, { once: true });
    // #endregion
    return () => {
      clearInterval(boardT);
      clearInterval(pushT);
      window.removeEventListener("mousemove", onMove);
    };
  }, [load, toast, pid]);

  const trolleys = board?.trolleys || [];
  const areaSeen = board?.areaSeen || {};
  const lines = project.lines || [];
  const test = board?.test || { on: false, fast: true, mode: "fast", tick: 0, made: 0, recs: 0 };
  const stats = board?.stats || { lines: 0, procs: 0, areas: 0, online: 0, stale: 0, total: 0 };
  const offlineMin = board?.offlineMin || Math.round((project.cfg?.offline || 0.5) * 60);

  const byLine = useMemo(() => {
    const map = {};
    trolleys.forEach((t) => {
      const key = t.lineCode || ((project.lines || [])[0]?.id || "");
      (map[key] ||= []).push(t);
    });
    return map;
  }, [trolleys, project.lines]);

  useEffect(() => {
    const el = linesRef.current;
    if (!el) return undefined;
    let raf = 0;
    let last = -1;
    const sync = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const h = Math.round(el.getBoundingClientRect().height);
        if (last >= 0 && Math.abs(h - last) < 2) return;
        last = h;
        resizeNRef.current += 1;
        setLinesH(h);
        // #region agent log
        fetch("http://127.0.0.1:7259/ingest/42287c2f-2aca-4b5e-a241-93e737ef8f41", { method: "POST", headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "537a1e" }, body: JSON.stringify({ sessionId: "537a1e", runId: "post-fix", hypothesisId: "C", location: "Board.jsx:resize", message: "lines-resize", data: { n: resizeNRef.current, h, applied: true }, timestamp: Date.now() }) }).catch(() => {});
        // #endregion
      });
    };
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [lines.length, pid]);

  const shownRecs = recs;

  const tzOpts = useMemo(() => {
    const seen = new Map(uniqueOptions(recs, "tz", (r) => `${r.tz} ${r.seat || ""}`.trim()));
    (project.trolleys || []).forEach((t) => {
      if (t.tz && !seen.has(t.tz)) seen.set(t.tz, `${t.tz} ${t.name || ""}`.trim());
    });
    return sortedOpts(seen);
  }, [recs, project.trolleys]);
  const lineOpts = useMemo(() => {
    const seen = new Map(uniqueOptions(recs, "line"));
    (project.lines || []).forEach((l) => {
      if (l.id && !seen.has(l.id)) seen.set(l.id, l.name || l.id);
    });
    return sortedOpts(seen);
  }, [recs, project.lines]);
  const areaOpts = useMemo(() => {
    const seen = new Map();
    (project.lines || []).forEach((l) => {
      (l.procs || []).forEach((g) => {
        (g.areas || []).forEach((a) => {
          if (a?.id && !seen.has(a.id)) seen.set(a.id, String(a.name || "").trim() || "-");
        });
      });
    });
    recs.forEach((r) => {
      if (r.area && !seen.has(r.area)) seen.set(r.area, areaLabel(r));
    });
    return sortedOpts(seen);
  }, [recs, project.lines]);
  const pcOpts = useMemo(() => {
    const seen = new Map(uniqueOptions(recs, "pc", (r) => `${r.pn || ""} ${r.pc || ""}`.trim()));
    (project.lines || []).forEach((l) => {
      (l.procs || []).forEach((g) => {
        if (g.code && !seen.has(g.code)) seen.set(g.code, `${g.name || ""} ${g.code}`.trim());
      });
    });
    return sortedOpts(seen);
  }, [recs, project.lines]);

  const pageCount = Math.max(1, Math.ceil((recTotal || 0) / PAGE_SIZE));
  const showPager = recTotal > PAGE_SIZE;

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  function applyColF(key, value) {
    setColF((f) => ({ ...f, [key]: value }));
    setPage(1);
  }

  function applyTime(which, value) {
    const nextFrom = which === "from" ? value : timeFrom;
    const nextTo = which === "to" ? value : timeTo;
    if (nextFrom && nextTo && nextTo < nextFrom) {
      toast("结束时间不能小于开始时间");
      return;
    }
    if (which === "from") setTimeFrom(value);
    else setTimeTo(value);
    setPage(1);
  }

  function resetFilters() {
    setQRec("");
    setMerge(false);
    setColF({ tz: "", line: "", area: "", pc: "" });
    setTimeFrom("");
    setTimeTo("");
    setPage(1);
  }

  const pushQueryExtra = {
    startTime: timeFrom || undefined,
    endTime: timeTo || undefined,
    tz: colF.tz || undefined,
    line: colF.line || undefined,
    area: colF.area || undefined,
    pc: colF.pc || undefined,
  };

  async function run(fn, okMsg, okK) {
    try {
      await fn();
      await load();
      if (okMsg) toast(okMsg, okK);
    } catch (e) {
      toast(e.message);
    }
  }

  function replyText(res) {
    const parts = [];
    if (res?.code != null && res.code !== "") parts.push(`code: ${res.code}`);
    if (res?.msg != null && String(res.msg).trim() !== "") parts.push(`msg: ${res.msg}`);
    return parts.join("  ") || (res?.ok ? "客户接口已接收" : "未返回 code / msg");
  }

  async function pushRow(r) {
    if (pushingId != null) return;
    setPushingId(r.id);
    // #region agent log
    fetch("http://127.0.0.1:7277/ingest/5e8a966a-6491-48f9-aacb-578ada25c074", { method: "POST", headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "7c60c2" }, body: JSON.stringify({ sessionId: "7c60c2", runId: "post-fix", hypothesisId: "E", location: "Board.jsx:pushRow", message: "click-start", data: { rid: r.id, test: r.test, src: r.src }, timestamp: Date.now() }) }).catch(() => {});
    // #endregion
    try {
      const res = await api.pushOne(pid, r.id);
      // #region agent log
      fetch("http://127.0.0.1:7277/ingest/5e8a966a-6491-48f9-aacb-578ada25c074", { method: "POST", headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "7c60c2" }, body: JSON.stringify({ sessionId: "7c60c2", runId: "pre-fix", hypothesisId: "A,D", location: "Board.jsx:pushRow", message: "push-reply", data: { rid: r.id, test: r.test, src: r.src, ok: res?.ok, code: res?.code, msg: res?.msg, httpStatus: res?.httpStatus, status: res?.status }, timestamp: Date.now() }) }).catch(() => {});
      // #endregion
      await load();
      toast(replyText(res), res.ok ? "推送成功" : "推送失败", 6000);
    } catch (e) {
      // #region agent log
      fetch("http://127.0.0.1:7277/ingest/5e8a966a-6491-48f9-aacb-578ada25c074", { method: "POST", headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "7c60c2" }, body: JSON.stringify({ sessionId: "7c60c2", runId: "pre-fix", hypothesisId: "E,F", location: "Board.jsx:pushRow", message: "push-error", data: { rid: r.id, test: r.test, err: String(e && e.message || e) }, timestamp: Date.now() }) }).catch(() => {});
      // #endregion
      toast(e.message, "推送失败", 6000);
    } finally {
      setPushingId(null);
    }
  }

  function dropOn(line, proc, area) {
    return {
      onDragOver: (e) => { e.preventDefault(); e.stopPropagation(); e.currentTarget.classList.add("dragover"); },
      onDragLeave: (e) => e.currentTarget.classList.remove("dragover"),
      onDrop: async (e) => {
        e.preventDefault();
        e.stopPropagation();
        e.currentTarget.classList.remove("dragover");
        const tz = e.dataTransfer.getData("text/tz");
        if (!tz) return;
        const target = area || proc.areas?.[0];
        try {
          await api.placeTrolley(pid, tz, { lineCode: line.id, procCode: proc.code, areaCode: target?.id || "" });
          await load();
          const where = [line.name, proc.name, target?.name].filter(Boolean).join("·");
          toast(`${tz} 已手动标位 → ${where}（来源→手动，已触发一次推送）`, "手动干预");
        } catch (err) {
          toast(err.message);
        }
      },
    };
  }

  async function viewPayload(id) {
    try {
      const data = await api.pushPayload(pid, id);
      setModal(
        <Modal title={"推送报文 · 序号 " + id} wide onClose={() => setModal(null)}
          foot={<button className="btn" onClick={() => setModal(null)}>关闭</button>}>
          <div className="alert">按本项目报文模板生成、推送给客户接口的 JSON。</div>
          <pre className="mono" style={{ margin: 0, background: "var(--paper-alt)", border: "1px solid var(--line)", borderRadius: 8, padding: 14, overflow: "auto" }}>
            {JSON.stringify(data.payload || {}, null, 2)}
          </pre>
        </Modal>
      );
    } catch (e) {
      toast(e.message);
    }
  }

  function editRec(r) {
    const procs = [];
    (project.lines || []).forEach((l) => l.procs.forEach((g) => procs.push({ ...g, lineId: l.id })));
    setModal(
      <Modal title={"修改推送记录 · 序号 " + r.id} wide onClose={() => setModal(null)}
        foot={
          <>
            <button className="btn" onClick={() => setModal(null)}>取消</button>
            <button className="btn primary" onClick={async () => {
              const procCode = document.getElementById("erProc").value;
              const lineCode = document.getElementById("erLine").value.trim();
              const areaCode = document.getElementById("erArea").value.trim();
              const sportState = document.getElementById("erState").value;
              try {
                await api.editPush(pid, r.id, { procCode, lineCode, areaCode, sportState });
                setModal(null);
                await load();
                toast("原记录已标记「已修改」，并新增一条手动记录推送给客户", "推送记录");
              } catch (e) {
                toast(e.message);
              }
            }}>保存并推送</button>
          </>
        }>
        <div className="alert warn">保存后<b>保留原记录并标记「已修改」</b>，同时<b>新增一条来源＝手动·手动编辑的记录</b>并触发一次向客户的推送，保证推送历史可追溯（PRD v0.6）。</div>
        <div className="grid g2">
          <div className="field">
            <label>识别工序</label>
            <select className="input" id="erProc" defaultValue={r.pc} onChange={(e) => {
              const g = procs.find((x) => x.code === e.target.value);
              if (g) document.getElementById("erLine").value = g.lineId;
            }}>
              {procs.map((g) => <option key={g.lineId + g.code} value={g.code}>{g.name} · {g.code}</option>)}
            </select>
          </div>
          <div className="field">
            <label>运动状态</label>
            <select className="input" id="erState" defaultValue={r.state}>
              <option>运动</option>
              <option>静止</option>
            </select>
          </div>
          <div className="field"><label>区域 ID</label><input className="input mono" id="erArea" defaultValue={r.area} /></div>
          <div className="field"><label>关联生产线</label><input className="input mono" id="erLine" defaultValue={r.line} /></div>
        </div>
      </Modal>
    );
  }

  function confirmClear() {
    setModal(
      <Modal title="清除测试数据" onClose={() => setModal(null)}
        foot={
          <>
            <button className="btn" onClick={() => setModal(null)}>取消</button>
            <button className="btn danger" onClick={async () => {
              try {
                const r = await api.testClear(pid);
                setModal(null);
                await load();
                toast(`已清除 ${r.pushes || 0} 条测试记录`, "测试数据");
              } catch (e) { toast(e.message); }
            }}>确认清除</button>
          </>
        }>
        <div className="alert err">将按<b>「测试」标识</b>删除本项目由测试模式生成的推送记录与模拟原始数据，<b>不可恢复</b>。{test.on ? "当前测试仍在运行，将先停止测试。" : ""}</div>
      </Modal>
    );
  }

  return (
    <div>
      <div className="kicker">模块 E · 项目内部</div>
      <div className="row between">
        <h1 className="page-title">主视图 · 实时监控 + 推送记录</h1>
        <div className="row wrap">
          <span className={"tag " + (board?.push ? "ok" : "err")}>{board?.push ? "● 自动推送运行中" : "● 自动推送已停止"}</span>
          <button className="btn sm" onClick={() => run(() => api.runtime(pid, { push: !board?.push }), board?.push ? "自动推送已停止，期间数据将积压待批量补推" : "自动推送已开启")}>{board?.push ? "停止推送" : "开启推送"}</button>
          <button className="btn sm" onClick={() => run(() => api.runtime(pid, { monitor: !board?.monitor }), board?.monitor ? "文件夹监测已停止" : "文件夹监测已启动")}>{board?.monitor ? "停止监测" : "启动监测"}</button>
          <button className="btn sm" onClick={() => run(async () => {
            const r = await api.batchPush(pid);
            toast(`已补推积压数据：${r.success}/${r.total} 条`, "批量补推");
          })}>手动批量推送</button>
          <button className={"btn sm " + (test.on ? "danger" : "primary")} onClick={() => {
            // #region agent log
            fetch("http://127.0.0.1:7259/ingest/42287c2f-2aca-4b5e-a241-93e737ef8f41", { method: "POST", headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "537a1e" }, body: JSON.stringify({ sessionId: "537a1e", runId: "post-fix", hypothesisId: "A,B", location: "Board.jsx:testBtn", message: "test-click", data: { turningOff: !!test.on, cursor: getComputedStyle(document.body).cursor }, timestamp: Date.now() }) }).catch(() => {});
            // #endregion
            run(
              () => test.on ? api.testStop(pid) : api.testStart(pid),
              test.on ? "测试模式已停止，已生成数据保留（可点「清除测试数据」清理）" : "测试模式已启动：已打开自动推送，测试数据将上传客户接口；看板按推进节奏走动，台座每 10 分钟再续推一次",
              "测试模式"
            );
          }}>{test.on ? "■ 停止测试" : "▶ 测试"}</button>
          <select className="input" style={{ width: 340, height: 32 }} value={test.mode || (test.fast ? "fast" : "real")} onChange={(e) => run(() => api.testSpeed(pid, e.target.value))}>
            <option value="fast">演示加速 推进 6s / 台座推送 10 分钟</option>
            <option value="real">真实节奏 推进 2min / 台座推送 10 分钟</option>
            <option value="hour">慢速 推进 1 小时 / 台座推送 10 分钟</option>
          </select>
          {(test.made || test.recs) ? <button className="btn sm" onClick={confirmClear}>清除测试数据</button> : null}
        </div>
      </div>
      <p className="sec-sub">上方看板由最新位置驱动：绿色＝{offlineMin} 分钟内在线，灰色＝停留超时；hover 台车可看最后上报时间与停留时长。可拖拽台车卡片手动标位（来源→手动·手动拖拽并触发一次推送）。下方推送记录含 <b>实时上报</b> / <b>保持推送</b> / <b>手动编辑 / 手动拖拽</b>。</p>

      {(test.on || test.made || test.recs) ? (
        <div className="testbar">
          <span className={"tag " + (test.on ? "info" : "")}>{test.on ? "● 测试模式运行中" : "○ 测试模式已停止"}</span>
          <span>推进节奏 <b>{test.advLabel}</b></span>
          <span>台座推送 <b>{test.keepLabel}</b></span>
          <span>已推进 <b>{test.tick}</b> 轮</span>
          <span>测试记录 <b>{test.recs}</b> 条</span>
          <span style={{ color: "var(--t3)" }}>规则：每台车从本产线第一道工序按序走到最后一道，不得跳过；走完回首工序；不跨产线；多区域随机选空闲区域；每区域仅一台车，被挤占者进「未分配」后仍按目标工序重入。测试台座每 10 分钟把当前位置推到客户接口。</span>
        </div>
      ) : null}

      {!lines.length ? (
        <>
          <div className="alert warn">本项目尚未完成映射表配置，实时监控无法渲染。请先完成<b>映射表</b>后再启用监测 / 推送。</div>
          <div className="row"><button className="btn primary" onClick={() => startWizard("edit", project, 2)}>去完成映射表</button></div>
        </>
      ) : (
        <div className="board">
          <div className="lines" ref={linesRef}>
            {lines.map((l) => {
              const list = byLine[l.id] || [];
              const un = list.filter((t) => t.unassigned);
              return (
                <div className="line" key={l.id}>
                  <div className="line-h">
                    <span className="nm">{l.name}</span>
                    <span className="pc" style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--t3)" }}>{l.id} · {l.procs.length} 工序</span>
                  </div>
                  <div className="procs" style={{ gridTemplateColumns: `repeat(${Math.max(l.procs.length, 1)}, 1fr)` }}>
                    {l.procs.map((g, gi) => {
                      const areas = g.areas || [];
                      return (
                        <div className="proc" key={l.id + g.code + g.name + gi}>
                          <div className="ph"><span>{g.name || "-"}</span></div>
                          {areas.length ? areas.map((a) => {
                            const here = list.filter((t) => !t.unassigned && t.procCode === g.code && t.areaCode === a.id && (!t.procName || t.procName === g.name));
                            const occ = here[0];
                            const seenKey = `${l.id}|${g.code}|${g.name || ""}|${a.id}`;
                            const remembered = areaSeen[seenKey];
                            const last = occ?.last || remembered?.last || "";
                            const ago = occ
                              ? (occ.agoSec != null ? occ.agoSec : (occ.mins != null ? occ.mins * 60 : null))
                              : (remembered?.agoSec != null ? remembered.agoSec : null);
                            const hasSeen = !!(last && last !== "—") || ago != null;
                            const tipTz = occ?.tz || remembered?.tz || "";
                            return (
                              <div className="area-slot" key={a.id} {...dropOn(l, g, a)}>
                                <div className="an">
                                  <span className="an-name">{String(a.name || "").trim() || "-"}</span>
                                  {hasSeen ? (
                                    <span
                                      className={"an-ago" + (occ && !occ.online ? " warn" : "") + (!occ ? " left" : "")}
                                      title={`最后识别 ${last || "—"} · 距今 ${formatAgo(ago)}${tipTz ? ` · 台车 ${tipTz}` : ""}${occ ? "" : "（已离开）"}`}
                                    >
                                      {lastTimeShort(last)} · 间隔 {formatAgo(ago)}
                                    </span>
                                  ) : (
                                    <span className="an-ago idle">空闲</span>
                                  )}
                                </div>
                                {here.map((t) => <TzCard key={t.tz} t={t} />)}
                              </div>
                            );
                          }) : (
                            <div className="area-slot empty" {...dropOn(l, g, null)}>
                              <div className="an">拖到此处</div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {un.length ? (
                    <div className="unassigned">
                      <span className="lb">未分配</span>
                      {un.map((t) => <TzCard key={t.tz} t={t} />)}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
          <aside className="board-side" style={linesH ? { "--lines-h": `${linesH}px` } : undefined}>
            <h4>台车列表 <span className="hint">· 可拖拽定位</span></h4>
            <div className="tz-stats">
              <div className="tz-stat">
                <div className="n">{stats.total}</div>
                <div className="l">总台车</div>
              </div>
              <div className="tz-stat">
                <div className="n" style={{ color: "var(--ok)" }}>{stats.online}</div>
                <div className="l">在线</div>
              </div>
              <div className="tz-stat">
                <div className="n" style={{ color: "var(--warn)" }}>{stats.stale}</div>
                <div className="l">停留超时</div>
              </div>
            </div>
            <div className="tz-list">{trolleys.map((t) => <TzCard key={t.tz} t={t} list />)}</div>
          </aside>
        </div>
      )}

      <div className="mt24">
        <div className="sec-h">推送记录（历史推送数据）</div>
        <p className="sec-t">发往客户接口的全量记录：查看报文、修改（来源→手动并触发推送）、单条 P2 推送、导出 Excel。</p>
      </div>
      <div className="toolbar">
        <input className="search" placeholder="搜索工序 / 台车 / 产线 / 区域 / 状态" value={qRec} onChange={(e) => { setQRec(e.target.value); setPage(1); }} />
        <label className="mini-note" style={{ margin: 0 }}>开始</label>
        <input className="input" type="datetime-local" style={{ width: 196, height: 32 }} value={timeFrom} onChange={(e) => applyTime("from", e.target.value)} />
        <label className="mini-note" style={{ margin: 0 }}>结束</label>
        <input className="input" type="datetime-local" style={{ width: 196, height: 32 }} value={timeTo} onChange={(e) => applyTime("to", e.target.value)} />
        <button className="btn sm" onClick={() => { setMerge((m) => !m); setPage(1); toast(merge ? "已关闭合并，展示全部记录" : "已启用合并：同台车同位置连续相同数据合并为一条"); }}>{merge ? "✓ 合并连续重复" : "合并连续重复"}</button>
        <button className="btn sm" onClick={resetFilters}>重置</button>
        <div style={{ flex: 1 }} />
        <button className="btn sm" onClick={() => downloadAuth(api.exportPushesUrl(pid, qRec, merge, pushQueryExtra), `${pid}-push-records.xlsx`).then(() => toast("已导出当前筛选结果 push-records.xlsx")).catch((e) => toast(e.message))}>⤒ 导出 Excel</button>
      </div>
      <div className="card pad0">
        <div className="tb-wrap" style={{ maxHeight: 840 }}>
          <table className="tb">
            <thead>
              <tr>
                <th className="num">序号</th>
                <th className="filterable">
                  识别工序
                  <HeaderFilter value={colF.pc} onChange={(v) => applyColF("pc", v)} options={pcOpts} placeholder="全部工序" />
                </th>
                <th className="filterable">
                  台车
                  <HeaderFilter value={colF.tz} onChange={(v) => applyColF("tz", v)} options={tzOpts} placeholder="全部台车" />
                </th>
                <th className="filterable">
                  产品线
                  <HeaderFilter value={colF.line} onChange={(v) => applyColF("line", v)} options={lineOpts} placeholder="全部产品线" />
                </th>
                <th className="filterable">
                  区域
                  <HeaderFilter value={colF.area} onChange={(v) => applyColF("area", v)} options={areaOpts} placeholder="全部区域" />
                </th>
                <th>状态</th>
                <th>识别时间</th>
                <th>数据来源</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {shownRecs.map((r) => (
                <tr key={r.id}>
                  <td className="num">{r.no}</td>
                  <td>{r.pn} <span className="mono" style={{ color: "var(--t3)" }}>{r.pc}</span></td>
                  <td className="mono">{r.tz} <span style={{ fontFamily: "var(--sans)", color: "var(--t2)" }}>{r.seat}</span></td>
                  <td className="mono">{r.line}</td>
                  <td>{areaLabel(r)}</td>
                  <td><span className={"tag" + (r.state === "运动" ? " info" : "")}>{r.state}</span></td>
                  <td className="mono">{r.time}</td>
                  <td>
                    <span className={"tag " + (r.src === "自动" ? "ok" : "warn")}>{r.src}</span>
                    {r.test ? <span className="src-edited" style={{ color: "var(--info)", borderColor: "rgba(0,105,203,.35)", background: "rgba(0,105,203,.06)" }}>测试</span> : null}
                    {r.edited ? <span className="src-edited">已修改</span> : null}
                    <span className="src-sub">{srcKind(r)}</span>
                  </td>
                  <td>
                    <div className="acts">
                      <button className="btn sm" onClick={() => viewPayload(r.id)}>查看报文</button>
                      <button className="btn sm" onClick={() => editRec(r)}>修改</button>
                      <button className="btn sm" disabled={pushingId != null} onClick={() => pushRow(r)}>
                        {pushingId === r.id ? "推送中…" : "推送"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!shownRecs.length ? <div className="empty">无匹配推送记录。</div> : null}
        </div>
        {showPager ? (
          <div className="pager">
            <button className="btn sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>上一页</button>
            <span>第 {page} / {pageCount} 页 · 共 {recTotal} 条</span>
            <button className="btn sm" disabled={page >= pageCount} onClick={() => setPage((p) => Math.min(pageCount, p + 1))}>下一页</button>
          </div>
        ) : null}
      </div>
      {modal}
    </div>
  );
}
