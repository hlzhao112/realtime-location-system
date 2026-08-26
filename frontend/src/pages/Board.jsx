import { useCallback, useEffect, useMemo, useState } from "react";
import { api, downloadAuth } from "../api";
import { countAreas, countProcs } from "../helpers.jsx";
import { Modal } from "../ui";

function srcKind(r) {
  return r.k || (r.src === "手动" ? "手动编辑" : "实时上报");
}

function TzCard({ t, list, onDragStart }) {
  const stale = !t.online;
  const mins = t.mins == null ? "—" : t.mins;
  const tip = `最后上报 ${t.last || "—"} · 停留 ${mins} min${t.areaCode ? " · 区域 " + t.areaCode : ""}${t.reissue ? " · 连续补发×" + t.reissue : ""} · 来源 ${t.src}`;
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
    </div>
  );
}

export default function Board({ project, toast, startWizard }) {
  const [board, setBoard] = useState(null);
  const [recs, setRecs] = useState([]);
  const [qRec, setQRec] = useState("");
  const [merge, setMerge] = useState(false);
  const [modal, setModal] = useState(null);
  const pid = project.pid;

  const load = useCallback(async () => {
    const [b, p] = await Promise.all([api.board(pid), api.pushes(pid, qRec, merge)]);
    setBoard(b);
    setRecs(p.items || []);
  }, [pid, qRec, merge]);

  useEffect(() => {
    load().catch((e) => toast(e.message));
    const t = setInterval(() => load().catch(() => {}), 2000);
    return () => clearInterval(t);
  }, [load, toast]);

  const trolleys = board?.trolleys || [];
  const test = board?.test || { on: false, fast: true, tick: 0, made: 0, recs: 0 };
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

  async function run(fn, okMsg, okK) {
    try {
      await fn();
      await load();
      if (okMsg) toast(okMsg, okK);
    } catch (e) {
      toast(e.message);
    }
  }

  function dropOn(line, proc) {
    return {
      onDragOver: (e) => { e.preventDefault(); e.currentTarget.classList.add("dragover"); },
      onDragLeave: (e) => e.currentTarget.classList.remove("dragover"),
      onDrop: async (e) => {
        e.preventDefault();
        e.currentTarget.classList.remove("dragover");
        const tz = e.dataTransfer.getData("text/tz");
        if (!tz) return;
        const area = proc.areas?.[0];
        try {
          await api.placeTrolley(pid, tz, { lineCode: line.id, procCode: proc.code, areaCode: area?.id || "" });
          await load();
          toast(`${tz} 已手动标位 → ${line.name}·${proc.name}（来源→手动，已触发一次推送）`, "手动干预");
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

  const lines = project.lines || [];

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
          <button className={"btn sm " + (test.on ? "danger" : "primary")} onClick={() => run(
            () => test.on ? api.testStop(pid) : api.testStart(pid),
            test.on ? "测试模式已停止，已生成数据保留（可点「清除测试数据」清理）" : `测试模式已启动：每 ${test.advLabel || "6 秒"} 推进一个工序`,
            "测试模式"
          )}>{test.on ? "■ 停止测试" : "▶ 测试"}</button>
          <select className="input" style={{ width: 172, height: 32 }} value={test.fast ? "fast" : "real"} onChange={(e) => run(() => api.testSpeed(pid, e.target.value === "fast"))}>
            <option value="fast">演示加速 6s / 3s</option>
            <option value="real">真实节奏 2min / 1min</option>
          </select>
          {(test.made || test.recs) ? <button className="btn sm" onClick={confirmClear}>清除测试数据</button> : null}
        </div>
      </div>
      <p className="sec-sub">上方看板由最新位置驱动：绿色＝{offlineMin} 分钟内在线，灰色＝停留超时；hover 台车可看最后上报时间与停留时长。可拖拽台车卡片手动标位（来源→手动·手动拖拽并触发一次推送）。下方推送记录含 <b>实时上报</b> / <b>保持推送</b> / <b>手动编辑 / 手动拖拽</b>。</p>

      {(test.on || test.made || test.recs) ? (
        <div className="testbar">
          <span className={"tag " + (test.on ? "info" : "")}>{test.on ? "● 测试模式运行中" : "○ 测试模式已停止"}</span>
          <span>推进节奏 <b>{test.advLabel}</b></span>
          <span>保持推送 <b>{test.keepLabel}</b></span>
          <span>已推进 <b>{test.tick}</b> 轮</span>
          <span>测试记录 <b>{test.recs}</b> 条</span>
          <span style={{ color: "var(--t3)" }}>规则：每台车按本产线工序顺序前进，走完回首工序；不跨产线；多区域随机选一个空闲区域；每区域仅一台车，被挤占者进「未分配」</span>
        </div>
      ) : null}

      {!lines.length ? (
        <>
          <div className="alert warn">本项目尚未完成映射表配置，实时监控无法渲染。请先完成<b>映射表</b>后再启用监测 / 推送。</div>
          <div className="row"><button className="btn primary" onClick={() => startWizard("edit", project, 2)}>去完成映射表</button></div>
        </>
      ) : (
        <div className="board">
          <div className="lines">
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
                    {l.procs.map((g) => {
                      const here = list.filter((t) => !t.unassigned && t.procCode === g.code);
                      return (
                        <div className="proc" key={g.code} {...dropOn(l, g)}>
                          <div className="ph"><span>{g.name}</span><span className="pc">{g.code}</span></div>
                          {here.map((t) => <TzCard key={t.tz} t={t} />)}
                          <div className="pc">{(g.areas || []).map((a) => a.id).join(" · ")}</div>
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
          <div>
            <div className="side-box">
              <h4>产线工序统计</h4>
              <div className="mini"><span>生产线</span><b>{stats.lines} 条</b></div>
              <div className="mini"><span>工序分区</span><b>{stats.procs || countProcs(project)} 个</b></div>
              <div className="mini"><span>区域</span><b>{stats.areas || countAreas(project)} 个</b></div>
              <div className="mini"><span>推送模板</span><b style={{ color: "var(--brand)" }}>{project.tpl?.custom ? "自定义" : "默认模板"}</b></div>
            </div>
            <div className="side-box">
              <h4>台车实时状态</h4>
              <div className="mini"><span>在线</span><b style={{ color: "var(--brand)" }}>{stats.online}</b></div>
              <div className="mini"><span>停留超时</span><b style={{ color: "var(--t3)" }}>{stats.stale}</b></div>
              <div className="mini"><span>总台车</span><b>{stats.total}</b></div>
            </div>
            <div className="side-box">
              <h4>全部台车一览 <span style={{ fontSize: 11, color: "var(--t3)", fontWeight: 400 }}>· 可拖拽定位</span></h4>
              <div className="tz-list">{trolleys.map((t) => <TzCard key={t.tz} t={t} list />)}</div>
            </div>
          </div>
        </div>
      )}

      <div className="mt24">
        <div className="sec-h">推送记录（历史推送数据）</div>
        <p className="sec-t">发往客户接口的全量记录：查看报文、修改（来源→手动并触发推送）、单条 P2 推送、导出 Excel。</p>
      </div>
      <div className="toolbar">
        <input className="search" placeholder="搜索工序 / 台车 / 产线 / 区域 / 状态" value={qRec} onChange={(e) => setQRec(e.target.value)} />
        <button className="btn sm" onClick={() => { setMerge((m) => !m); toast(merge ? "已关闭合并，展示全部记录" : "已启用合并：同台车同位置连续相同数据合并为一条"); }}>{merge ? "✓ 合并连续重复" : "合并连续重复"}</button>
        <div style={{ flex: 1 }} />
        <button className="btn sm" onClick={() => downloadAuth(api.exportPushesUrl(pid, qRec, merge), `${pid}-push-records.xlsx`).then(() => toast("已导出当前筛选结果 push-records.xlsx")).catch((e) => toast(e.message))}>⤒ 导出 Excel</button>
      </div>
      <div className="card pad0">
        <div className="tb-wrap" style={{ maxHeight: 420 }}>
          <table className="tb">
            <thead>
              <tr>
                <th className="num">序号</th>
                <th>识别工序</th>
                <th>关联台座</th>
                <th>关联生产线</th>
                <th>区域</th>
                <th>状态</th>
                <th>识别时间</th>
                <th>数据来源</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {recs.map((r) => (
                <tr key={r.id}>
                  <td className="num">{r.no}</td>
                  <td>{r.pn} <span className="mono" style={{ color: "var(--t3)" }}>{r.pc}</span></td>
                  <td className="mono">{r.tz} <span style={{ fontFamily: "var(--sans)", color: "var(--t2)" }}>{r.seat}</span></td>
                  <td className="mono">{r.line}</td>
                  <td className="mono">{r.area}</td>
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
                      <button className="btn sm" onClick={() => run(() => api.pushOne(pid, r.id), `已对序号 ${r.id} 执行单条 P2 推送`, "推送")}>推送</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!recs.length ? <div className="empty">无匹配推送记录。</div> : null}
        </div>
      </div>
      {modal}
    </div>
  );
}
