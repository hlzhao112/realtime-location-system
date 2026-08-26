import { useState } from "react";
import { api, downloadAuth } from "../api";
import { ALL_FIELDS, countAreas, countEpcs, countNos, countProcs, step1Done, step2Done, toSaveBody } from "../helpers.jsx";
import { Field, Modal } from "../ui";

function fset(draft, setDraft, key, val) {
  setDraft({ ...draft, cfg: { ...draft.cfg, [key]: val, ...(key === "pid" ? { pid: val } : {}), ...(key === "name" ? { name: val } : {}) }, ...(key === "pid" ? { pid: val } : {}), ...(key === "name" ? { name: val } : {}) });
}

export default function Wizard({ draft, setDraft, mode, step, setStep, onCancel, onSaved, toast }) {
  const [tab, setTab] = useState(0);
  const [sel, setSel] = useState({ line: 0, proc: 0, area: 0 });
  const [busy, setBusy] = useState(false);
  const [imp, setImp] = useState(null);
  const [edit, setEdit] = useState(null);
  const s1 = step1Done(draft);
  const s2 = step2Done(draft);

  const line = draft.lines[sel.line];
  const proc = line?.procs?.[sel.proc];
  const area = proc?.areas?.[sel.area];

  function go(n) {
    if (n > 1 && !s1) return toast("Step1 为必填：请先补全 PID、项目名称与监控文件夹路径");
    if (n > 2 && !s2) return toast("Step2 为必填：至少一条产线映射，且台车映射不能为空");
    setStep(n);
  }

  async function finish(skipTpl) {
    if (!s1) return go(1);
    if (!s2) return go(2);
    setBusy(true);
    try {
      const body = toSaveBody(draft);
      const saved = mode === "edit" ? await api.updateProject(draft.cfg.pid, body) : await api.createProject(body);
      if (skipTpl) toast("已跳过报文模板，按系统默认模板生效", "Step3");
      else toast(mode === "edit" ? "项目配置已保存" : `项目「${saved.name}」创建成功`, "完成");
      if (saved.ingestTokenOnce) toast("请保存设备上报 Token（仅显示一次）", saved.ingestTokenOnce);
      onSaved(saved);
    } catch (e) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wiz">
      <div className="kicker">模块 C + 模块 D</div>
      <h1 className="page-title">{mode === "edit" ? `编辑项目 · ${draft.name || draft.cfg.name}` : mode === "copy" ? "复制项目" : "新建项目"}</h1>
      <p className="sec-sub">共三步：① 项目基本配置（必填）→ ② 建立映射表（必填）→ ③ 报文模板（已有默认值，可跳过）。保存后进入项目信息。</p>
      <div className="steps">
        {[
          ["项目基本配置", "必填 · 采集与鉴权参数", s1],
          ["建立映射表", "必填 · 工序·标签 + 台车", s2],
          ["报文模板", "有默认值 · 可跳过", true],
        ].map((x, i) => {
          const n = i + 1;
          const cls = step === n ? "step on" : "step" + (x[2] ? " done" : "");
          return (
            <button key={n} className={cls} onClick={() => go(n)}>
              <span className="idx">{x[2] && step !== n ? "✓" : n}</span>
              <span>
                <span className="tt">{x[0]}</span>
                <span className="ss">{x[1]}</span>
              </span>
            </button>
          );
        })}
      </div>

      {step === 1 && <Step1 draft={draft} setDraft={setDraft} />}
      {step === 2 && (
        <Step2
          draft={draft}
          setDraft={setDraft}
          tab={tab}
          setTab={setTab}
          sel={sel}
          setSel={setSel}
          line={line}
          proc={proc}
          area={area}
          toast={toast}
          setImp={setImp}
          setEdit={setEdit}
        />
      )}
      {step === 3 && <Step3 draft={draft} setDraft={setDraft} toast={toast} />}

      <div className="wiz-foot">
        <button className="btn" onClick={onCancel}>取消</button>
        <span className="note">{step === 3 ? "未做修改时按默认模板生效。" : step === 1 ? "PID 唯一且不可重复；AppSecret 保存后不回显。" : "两张表均支持文档导入或手工填写，可混用。"}</span>
        <div className="spacer" />
        {step > 1 && <button className="btn" onClick={() => go(step - 1)}>上一步</button>}
        {step === 3 && <button className="btn" onClick={() => finish(true)} disabled={busy}>跳过此步</button>}
        <button className="btn primary" disabled={busy} onClick={() => (step === 3 ? finish(false) : go(step + 1))}>
          {step === 3 ? "保存并进入项目" : "下一步"}
        </button>
      </div>

      {imp && (
        <ImportModal
          kind={imp}
          draft={draft}
          setDraft={setDraft}
          toast={toast}
          onClose={() => setImp(null)}
        />
      )}
      {edit && (
        <EditModal
          edit={edit}
          draft={draft}
          setDraft={setDraft}
          toast={toast}
          onClose={() => setEdit(null)}
        />
      )}
    </div>
  );
}

function Step1({ draft, setDraft }) {
  const c = draft.cfg;
  const set = (k, v) => fset(draft, setDraft, k, v);
  return (
    <>
      <div className="alert">工序顺序不在此配置，由 Step2 映射表中每条产线的工序列表排列决定。</div>
      <div className="fieldset">
        <div className="fh"><span className="t">项目基本信息</span><span className="s">必填</span></div>
        <div className="fb">
          <Field label="项目唯一 PID" req hint="英文数字，创建后不可改">
            <input className="input mono" value={c.pid} disabled={!!draft._lockedPid} onChange={(e) => set("pid", e.target.value.trim())} />
          </Field>
          <Field label="项目名称" req>
            <input className="input" value={c.name} onChange={(e) => set("name", e.target.value)} />
          </Field>
        </div>
      </div>
      <div className="fieldset">
        <div className="fh"><span className="t">采集参数</span><span className="s">本地文件夹监控</span></div>
        <div className="fb">
          <Field label="RFID 监控文件夹" req><input className="input mono" value={c.folder} onChange={(e) => set("folder", e.target.value)} /></Field>
          <Field label="JSON 备份目录"><input className="input mono" value={c.backup} onChange={(e) => set("backup", e.target.value)} /></Field>
          <Field label="扫描间隔（秒）"><input className="input" type="number" value={c.scan} onChange={(e) => set("scan", e.target.value)} /></Field>
          <Field label="文件稳定等待（秒）"><input className="input" type="number" value={c.stable} onChange={(e) => set("stable", e.target.value)} /></Field>
          <Field label="离线判定（小时）" hint="0.5 = 30 分钟无数据即离线"><input className="input" type="number" step="0.1" value={c.offline} onChange={(e) => set("offline", e.target.value)} /></Field>
          <Field label="单批台车上限"><input className="input" type="number" value={c.batch} onChange={(e) => set("batch", e.target.value)} /></Field>
        </div>
      </div>
      <div className="fieldset">
        <div className="fh"><span className="t">推送与容错</span></div>
        <div className="fb">
          <Field label="连续补发上限"><input className="input" type="number" value={c.resendMax} onChange={(e) => set("resendMax", e.target.value)} /></Field>
          <Field label="接口失败重试次数"><input className="input" type="number" value={c.retry} onChange={(e) => set("retry", e.target.value)} /></Field>
          <Field label="日志自动清理（天）"><input className="input" type="number" value={c.logClean} onChange={(e) => set("logClean", e.target.value)} /></Field>
        </div>
      </div>
      <div className="fieldset">
        <div className="fh"><span className="t">客户平台接口与鉴权</span><span className="s">推送是往客户传，不是收设备数据</span></div>
        <div className="fb c2">
          <Field label="AppID"><input className="input mono" value={c.appId} onChange={(e) => set("appId", e.target.value)} /></Field>
          <Field label="AppSecret" hint="保存后不回显，留空表示不修改"><input className="input" type="password" value={c.appSecret} onChange={(e) => set("appSecret", e.target.value)} /></Field>
          <Field label="Token 获取接口"><input className="input mono" value={c.tokenUrl} onChange={(e) => set("tokenUrl", e.target.value)} /></Field>
          <Field label="客户上报（数据推送）接口"><input className="input mono" value={c.pushUrl} onChange={(e) => set("pushUrl", e.target.value)} /></Field>
        </div>
      </div>
    </>
  );
}

function Step2({ draft, setDraft, tab, setTab, sel, setSel, line, proc, area, toast, setImp, setEdit }) {
  const items = [
    ["产线 " + (draft.lines || []).length, (draft.lines || []).length > 0],
    ["工序 " + countProcs(draft), countProcs(draft) > 0],
    ["区域 " + countAreas(draft), countAreas(draft) > 0],
    ["标签编号 " + countNos(draft), countNos(draft) > 0],
    ["EPC " + countEpcs(draft), countEpcs(draft) > 0],
    ["台车 " + (draft.trolleys || []).length, (draft.trolleys || []).length > 0],
  ];
  return (
    <>
      <div className="tabs">
        <button className={"tab" + (tab === 0 ? " on" : "")} onClick={() => setTab(0)}>
          映射表一 · 工序·标签映射<span className="cnt">{countNos(draft)} 编号 / {countEpcs(draft)} EPC</span>
        </button>
        <button className={"tab" + (tab === 1 ? " on" : "")} onClick={() => setTab(1)}>
          映射表二 · 台车映射<span className="cnt">{(draft.trolleys || []).length}</span>
        </button>
      </div>
      <div className="checklist" style={{ marginBottom: 12 }}>
        {items.map((x) => (
          <span key={x[0]} className={"chk" + (x[1] ? " ok" : "")}>
            <span className="b">{x[1] ? "✓" : ""}</span>
            {x[0]}
          </span>
        ))}
      </div>
      {tab === 0 ? (
        <MapOne draft={draft} setDraft={setDraft} sel={sel} setSel={setSel} line={line} proc={proc} area={area} toast={toast} setImp={setImp} setEdit={setEdit} />
      ) : (
        <MapTwo draft={draft} setDraft={setDraft} toast={toast} setImp={setImp} />
      )}
    </>
  );
}

function MapOne({ draft, setDraft, sel, setSel, line, proc, area, toast, setImp, setEdit }) {
  function addLine() {
    const n = draft.lines.length + 1;
    const lines = draft.lines.concat([{ id: "ZCX_" + String(n).padStart(3, "0"), name: n + "号生产线", procs: [] }]);
    setDraft({ ...draft, lines });
    setSel({ line: lines.length - 1, proc: 0, area: 0 });
  }
  function addProc() {
    if (!line) return toast("请先选产线");
    const g = { code: "GX_" + String((line.procs.length + 1) * 10).padStart(4, "0"), name: "新工序", order: line.procs.length + 1, areas: [] };
    line.procs = line.procs.concat([g]);
    setDraft({ ...draft, lines: [...draft.lines] });
    setSel({ ...sel, proc: line.procs.length - 1, area: 0 });
  }
  function addArea() {
    if (!proc) return toast("请先选工序");
    const a = { id: "QY_" + String(proc.areas.length + 1).padStart(3, "0"), name: "新区域", nos: ["", "", ""], epcs: [] };
    proc.areas = proc.areas.concat([a]);
    setDraft({ ...draft, lines: [...draft.lines] });
    setSel({ ...sel, area: proc.areas.length - 1 });
  }
  function moveProc(i, d) {
    const j = i + d;
    if (!line || j < 0 || j >= line.procs.length) return;
    const g = line.procs.splice(i, 1)[0];
    line.procs.splice(j, 0, g);
    line.procs.forEach((x, idx) => (x.order = idx + 1));
    setDraft({ ...draft, lines: [...draft.lines] });
    setSel({ ...sel, proc: j, area: 0 });
  }
  function setNo(j, v) {
    area.nos[j] = v;
    if (v && !draft.tagNos[v]) draft.tagNos[v] = [];
    setDraft({ ...draft, lines: [...draft.lines], tagNos: { ...draft.tagNos } });
  }
  return (
    <>
      <div className="alert">映射走<b>两级</b>：位置（产线 → 工序 → 区域）→ 蓝牙标签编号 → 多个 EPC。推荐双文件导入。</div>
      <div className="row mb12 wrap">
        <button className="btn primary" onClick={() => setImp("tags")}>文件导入（需 2 个文件）</button>
        <button className="btn" onClick={() => downloadAuth(api.templateUrl("pos"), "位置-编号映射表.xlsx")}>模板 ① 位置-编号</button>
        <button className="btn" onClick={() => downloadAuth(api.templateUrl("no"), "编号-映射表.xlsx")}>模板 ② 编号-映射</button>
      </div>
      <div className="drill">
        <div className="drill-col">
          <div className="drill-h"><span className="t">① 产线</span><button className="btn sm" onClick={addLine}>+ 产线</button></div>
          <div className="drill-b">
            {draft.lines.map((l, i) => (
              <button key={l.id + i} className={"drill-item" + (i === sel.line ? " on" : "")} onClick={() => setSel({ line: i, proc: 0, area: 0 })}>
                <span><span className="nm">{l.name}</span><br /><span className="sub">{l.id}</span></span>
                <span className="tail">
                  <span className="pen" onClick={(e) => { e.stopPropagation(); setEdit({ type: "line", i }); }}>✎</span>
                  <span className="x" onClick={(e) => { e.stopPropagation(); draft.lines.splice(i, 1); setDraft({ ...draft, lines: [...draft.lines] }); setSel({ line: 0, proc: 0, area: 0 }); }}>×</span>
                </span>
              </button>
            ))}
          </div>
        </div>
        <div className="drill-col">
          <div className="drill-h"><span className="t">② 工序（拖拽 / ↑↓ 即顺序）</span><button className="btn sm" onClick={addProc}>+ 工序</button></div>
          <div className="drill-b">
            {(line?.procs || []).map((g, i) => (
              <button key={g.code + i} className={"drill-item" + (i === sel.proc ? " on" : "")} onClick={() => setSel({ ...sel, proc: i, area: 0 })}>
                <span><span className="nm">{i + 1}. {g.name}</span><br /><span className="sub">{g.code}</span></span>
                <span className="tail">
                  <button className="btn sm" onClick={(e) => { e.stopPropagation(); moveProc(i, -1); }}>↑</button>
                  <button className="btn sm" onClick={(e) => { e.stopPropagation(); moveProc(i, 1); }}>↓</button>
                  <span className="pen" onClick={(e) => { e.stopPropagation(); setEdit({ type: "proc", i }); }}>✎</span>
                  <span className="x" onClick={(e) => { e.stopPropagation(); line.procs.splice(i, 1); line.procs.forEach((x, idx) => (x.order = idx + 1)); setDraft({ ...draft, lines: [...draft.lines] }); setSel({ ...sel, proc: 0, area: 0 }); }}>×</span>
                </span>
              </button>
            ))}
          </div>
        </div>
        <div className="drill-col">
          <div className="drill-h"><span className="t">③ 区域与标签编号</span><button className="btn sm" onClick={addArea}>+ 区域</button></div>
          <div className="drill-b">
            {(proc?.areas || []).map((a, i) => (
              <button key={a.id + i} className={"drill-item" + (i === sel.area ? " on" : "")} onClick={() => setSel({ ...sel, area: i })}>
                <span><span className="nm">{a.name}</span><br /><span className="sub">{a.id}</span></span>
                <span className="tail"><span className="tag">{(a.nos || []).filter(Boolean).length} 编号</span>
                  <span className="x" onClick={(e) => { e.stopPropagation(); proc.areas.splice(i, 1); setDraft({ ...draft, lines: [...draft.lines] }); }}>×</span>
                </span>
              </button>
            ))}
            {area && (
              <div className="mt16">
                <div className="mini-note" style={{ marginBottom: 8 }}>该区域标签编号（默认 3 个）。EPC 来自编号-映射表。</div>
                {(area.nos || []).map((no, j) => (
                  <div className="epc-row" key={j}>
                    <span className="idx">{j + 1}</span>
                    <input className="input mono" value={no} placeholder="B1" onChange={(e) => setNo(j, e.target.value.trim())} />
                    <span className={"tag" + ((draft.tagNos[no] || []).length ? " ok" : " err")}>{(draft.tagNos[no] || []).length} EPC</span>
                  </div>
                ))}
                <button className="btn sm" onClick={() => { area.nos = (area.nos || []).concat([""]); setDraft({ ...draft, lines: [...draft.lines] }); }}>+ 添加标签编号</button>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function MapTwo({ draft, setDraft, setImp }) {
  function setTz(i, k, v) {
    draft.trolleys[i][k] = v;
    setDraft({ ...draft, trolleys: [...draft.trolleys] });
  }
  return (
    <>
      <div className="alert">录入本项目<b>台车与读卡器</b>绑定。只需一个「台座编号映射表」。</div>
      <div className="row mb12 wrap">
        <button className="btn primary" onClick={() => setImp("tz")}>文件导入（1 个文件）</button>
        <button className="btn" onClick={() => downloadAuth(api.templateUrl("tz"), "港台座编号映射.xlsx")}>下载模板</button>
        <button className="btn" onClick={() => setDraft({ ...draft, trolleys: draft.trolleys.concat([{ tz: "TZ_" + (100 + draft.trolleys.length + 1), name: "台座 " + (draft.trolleys.length + 1), reader: "" }]) })}>+ 手工新增</button>
      </div>
      <div className="card pad0">
        <div className="tb-wrap">
          <table className="tb">
            <thead><tr><th>序号</th><th>台车 ID</th><th>台座名称</th><th>读卡器设备 ID</th><th></th></tr></thead>
            <tbody>
              {draft.trolleys.map((t, i) => (
                <tr key={i}>
                  <td className="num">{i + 1}</td>
                  <td><input className="input mono" value={t.tz} onChange={(e) => setTz(i, "tz", e.target.value)} /></td>
                  <td><input className="input" value={t.name} onChange={(e) => setTz(i, "name", e.target.value)} /></td>
                  <td><input className="input mono" value={t.reader} onChange={(e) => setTz(i, "reader", e.target.value)} /></td>
                  <td><button className="btn sm danger" onClick={() => { draft.trolleys.splice(i, 1); setDraft({ ...draft, trolleys: [...draft.trolleys] }); }}>删除</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!draft.trolleys.length && <div className="empty">尚无台车映射，请导入或手工新增。</div>}
        </div>
      </div>
    </>
  );
}

function Step3({ draft, setDraft, toast }) {
  const t = draft.tpl;
  function toggle(k, on) {
    const F = t.fields.slice();
    const i = F.indexOf(k);
    if (on && i < 0) F.push(k);
    if (!on && i >= 0) F.splice(i, 1);
    setDraft({ ...draft, tpl: { ...t, fields: F, custom: true } });
  }
  function gen() {
    if (!t.fields.length) return toast("请先勾选字段");
    setDraft({ ...draft, tpl: { ...t, json: "{\n" + t.fields.map((k) => `  "${k}": "\${${k}}"`).join(",\n") + "\n}", custom: true } });
  }
  return (
    <>
      <div className="alert">绝大多数项目直接跳过本步。仅当客户接口有特殊要求时修改。</div>
      <div className="fieldset">
        <div className="fh"><span className="t">字段勾选</span></div>
        <div className="fb c1">
          <div className="fchips">
            {ALL_FIELDS.map((k) => (
              <label key={k} className={"fchip" + (t.fields.includes(k) ? " on" : "")}>
                <input type="checkbox" checked={t.fields.includes(k)} onChange={(e) => toggle(k, e.target.checked)} />
                {k}
              </label>
            ))}
          </div>
          <div className="row wrap">
            <button className="btn sm" onClick={gen}>生成模板</button>
            <button className="btn sm" onClick={() => setDraft({ ...draft, tpl: { fields: ["areaCode", "beamAssetsCode", "beamLineCode", "identifyTime", "procedureCode", "sportState"], json: draft.tpl.json, custom: false } })}>恢复默认</button>
          </div>
        </div>
      </div>
      <div className="fieldset">
        <div className="fh"><span className="t">推送报文模板</span><span className="s">占位符形如 ${"{areaCode}"}</span></div>
        <div className="fb c1">
          <textarea className="input" rows="9" value={t.json} onChange={(e) => setDraft({ ...draft, tpl: { ...t, json: e.target.value, custom: true } })} />
        </div>
      </div>
    </>
  );
}

function EditModal({ edit, draft, setDraft, toast, onClose }) {
  const line = draft.lines[edit.i >= 0 ? (edit.type === "line" ? edit.i : undefined) : 0];
  const target = edit.type === "line" ? draft.lines[edit.i] : draft.lines.find((_, idx) => idx === (edit.line ?? 0))?.procs?.[edit.i];
  const [name, setName] = useStateSafe(edit.type === "line" ? draft.lines[edit.i]?.name : "");
  const [id, setId] = useStateSafe(edit.type === "line" ? draft.lines[edit.i]?.id : "");
  // simpler: read current
  const cur = edit.type === "line" ? draft.lines[edit.i] : null;
  const [nm, setNm] = useStateSafe(cur?.name || "");
  const [cd, setCd] = useStateSafe(cur?.id || "");
  if (edit.type === "line" && cur) {
    return (
      <Modal title={"编辑产线 · " + cur.name} onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" onClick={() => {
        if (!nm.trim() || !cd.trim()) return toast("产线名称与产线 ID 均为必填", "校验");
        if (draft.lines.some((x, i) => i !== edit.i && x.id === cd.trim())) return toast("产线 ID 已存在", "校验");
        cur.name = nm.trim(); cur.id = cd.trim();
        setDraft({ ...draft, lines: [...draft.lines] }); onClose();
      }}>保存</button></>}>
        <div className="grid g2">
          <Field label="产线名称"><input className="input" value={nm} onChange={(e) => setNm(e.target.value)} /></Field>
          <Field label="产线 ID"><input className="input mono" value={cd} onChange={(e) => setCd(e.target.value)} /></Field>
        </div>
      </Modal>
    );
  }
  const L = draft.lines.find((l) => l === line) || draft.lines[0];
  const g = L?.procs?.[edit.i];
  const [pn, setPn] = useStateSafe(g?.name || "");
  const [pc, setPc] = useStateSafe(g?.code || "");
  return (
    <Modal title={"编辑工序 · " + (g?.name || "")} onClose={onClose} foot={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" onClick={() => {
      if (!pn.trim() || !pc.trim()) return toast("工序名称与编码均为必填", "校验");
      const parent = draft.lines[ /* current selected line via edit */ 0];
      const host = draft.lines.find((l) => l.procs.includes(g)) || draft.lines[0];
      if (host.procs.some((x) => x !== g && x.code === pc.trim())) return toast("同一产线内工序编码重复", "校验");
      g.name = pn.trim(); g.code = pc.trim();
      setDraft({ ...draft, lines: [...draft.lines] }); onClose();
    }}>保存</button></>}>
      <div className="grid g2">
        <Field label="工序名称"><input className="input" value={pn} onChange={(e) => setPn(e.target.value)} /></Field>
        <Field label="工序编码"><input className="input mono" value={pc} onChange={(e) => setPc(e.target.value)} /></Field>
      </div>
    </Modal>
  );
}

function useStateSafe(v) {
  const [s, set] = useState(v);
  return [s, set];
}

function ImportModal({ kind, draft, setDraft, toast, onClose }) {
  const [pos, setPos] = useState(null);
  const [no, setNo] = useState(null);
  const [tz, setTz] = useState(null);
  const [prev, setPrev] = useState(null);
  const [busy, setBusy] = useState(false);
  const pid = draft.cfg.pid || "DRAFT";

  async function runPreview() {
    setBusy(true);
    try {
      if (kind === "tags") {
        if (!pos || !no) return toast("两个文件均为必需");
        const data = draft._lockedPid
          ? await api.previewTags(draft.cfg.pid, pos, no)
          : await api.previewTagsDraft(pos, no);
        setPrev(data);
      } else {
        if (!tz) return toast("请先选择文件");
        const data = draft._lockedPid
          ? await api.previewTz(draft.cfg.pid, tz)
          : await api.previewTzDraft(tz);
        setPrev(data);
      }
    } catch (e) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  }

  function apply() {
    if (!prev || !prev.ok) return;
    if (kind === "tags") {
      setDraft({ ...draft, lines: prev.payload.lines, tagNos: prev.payload.tagNos });
      toast("导入成功，已写入向导草稿", "双文件导入");
    } else {
      setDraft({ ...draft, trolleys: prev.payload });
      toast("导入成功：" + prev.payload.length + " 条台座", "导入");
    }
    onClose();
  }

  return (
    <Modal wide title={kind === "tags" ? "文档导入 · 映射表一" : "文档导入 · 映射表二"} onClose={onClose}
      foot={<><button className="btn" onClick={onClose}>取消</button>
        <button className="btn" disabled={busy} onClick={runPreview}>解析预览</button>
        <button className="btn primary" disabled={!prev?.ok} onClick={apply}>确认覆盖导入</button></>}>
      {kind === "tags" ? (
        <>
          <div className="alert">需要两个文件：位置-编号映射表 + 编号-映射表。缺一不可。预览接口需要项目已存在 PID；新建时建议先保存 Step1 或创建后再在项目信息中导入。</div>
          <Slot title="① 位置-编号映射表" file={pos} onFile={setPos} kind="pos" />
          <Slot title="② 编号-映射表" file={no} onFile={setNo} kind="no" />
        </>
      ) : (
        <>
          <div className="alert">只需一个台座编号映射表。</div>
          <Slot title="台座编号映射表" file={tz} onFile={setTz} kind="tz" />
        </>
      )}
      {prev && <PreviewBlock prev={prev} kind={kind} />}
    </Modal>
  );
}

function Slot({ title, file, onFile, kind }) {
  const names = { pos: "位置-编号映射表.xlsx", no: "编号-映射表.xlsx", tz: "港台座编号映射.xlsx" };
  return (
    <div className="fieldset">
      <div className="fh"><span className="t">{title}</span><span className="s">{file ? "已选择 " + file.name : "未选择文件"}</span></div>
      <div className="fb c1">
        <div className="row wrap">
          <label className={"btn sm" + (file ? "" : " primary")}>
            {file ? "重新选择" : "选择文件"}
            <input type="file" accept=".xlsx,.csv" hidden onChange={(e) => onFile(e.target.files[0])} />
          </label>
          <button className="btn sm" type="button" onClick={() => downloadAuth(api.templateUrl(kind), names[kind])}>下载模板</button>
          {file ? <span className="tag ok">已选择</span> : <span className="tag warn">待上传</span>}
        </div>
      </div>
    </div>
  );
}

function PreviewBlock({ prev, kind }) {
  return (
    <>
      <div className="checklist">
        {Object.entries(prev.stats || {}).map(([k, v]) => (
          <span className="chk ok" key={k}><span className="b">✓</span>{k} {v}</span>
        ))}
      </div>
      <div className="card pad0 mb12" style={{ marginTop: 12 }}>
        <div className="tb-wrap">
          <table className="tb">
            <thead><tr><th>结果</th><th>校验项</th><th>说明</th></tr></thead>
            <tbody>
              {(prev.checks || []).map((c, i) => (
                <tr key={i}>
                  <td><span className={"tag " + (c.level === "ok" ? "ok" : c.level === "warn" ? "warn" : "err")}>{c.level === "ok" ? "通过" : c.level === "warn" ? "提示" : "阻止"}</span></td>
                  <td>{c.title}</td>
                  <td>{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {kind === "tags" && prev.preview?.length ? (
        <div className="tb-wrap">
          <table className="tb">
            <thead><tr><th>产线</th><th>工序</th><th>区域</th><th>编号</th><th>EPC</th></tr></thead>
            <tbody>
              {prev.preview.map((r, i) => (
                <tr key={i}>
                  <td>{r.lineName}<br /><span className="mono">{r.lineId}</span></td>
                  <td>{r.procName}<br /><span className="mono">{r.procCode}</span></td>
                  <td>{r.areaName}<br /><span className="mono">{r.areaId}</span></td>
                  <td className="mono">{r.no}</td>
                  <td className="mono">{(r.epcs || [])[0]} {(r.epcs || []).length > 1 ? `等 ${r.epcs.length} 条` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {kind === "tz" && prev.preview?.length ? (
        <div className="tb-wrap">
          <table className="tb">
            <thead><tr><th>台座名称</th><th>编号</th><th>读卡器</th></tr></thead>
            <tbody>{prev.preview.map((t, i) => <tr key={i}><td>{t.name}</td><td className="mono">{t.tz}</td><td className="mono">{t.reader}</td></tr>)}</tbody>
          </table>
        </div>
      ) : null}
      {prev.diff?.length ? (
        <div className="tb-wrap" style={{ marginTop: 12 }}>
          <table className="tb">
            <thead><tr><th>对象</th><th className="num">当前</th><th className="num">导入后</th><th className="num">新增</th><th className="num">删除</th></tr></thead>
            <tbody>{prev.diff.map((d) => <tr key={d.object}><td>{d.object}</td><td className="num">{d.current}</td><td className="num">{d.incoming}</td><td className="num">{d.added}</td><td className="num">{d.removed}</td></tr>)}</tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}
