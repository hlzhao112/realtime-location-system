import { useState } from "react";
import { api, downloadAuth } from "../api";
import { ALL_FIELDS, countAreas, countEpcs, countNos, countProcs } from "../helpers.jsx";
import { Field, Modal } from "../ui";

const TABS = ["项目基本信息", "映射表", "报文模板"];

export default function Info({ project, setProject, toast, startWizard }) {
  const [tab, setTab] = useState(0);
  const [mapTab, setMapTab] = useState(0);
  const [q, setQ] = useState("");
  const [modal, setModal] = useState(null);
  const p = project;
  const counts = [`${(p.lines || []).length} 线 / ${countAreas(p)} 区`, `${countNos(p)} 编号 / ${countEpcs(p)} EPC`, p.tpl?.custom ? "自定义" : "默认"];

  return (
    <div>
      <div className="kicker">模块 C + 模块 D · 项目内部</div>
      <h1 className="page-title">项目信息</h1>
      <p className="sec-sub">默认展示已配置内容。可单项 / 单条编辑，或整体流程编辑回到三步向导。</p>
      <div className="toolbar">
        <div className="tabs">
          {TABS.map((t, i) => (
            <button key={t} className={"tab" + (tab === i ? " on" : "")} onClick={() => setTab(i)}>
              {t}<span className="cnt">{counts[i]}</span>
            </button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <button className="btn primary" onClick={() => startWizard("edit", p, tab + 1)}>整体流程编辑（三步向导）</button>
      </div>
      {tab === 0 && <Basic p={p} setProject={setProject} toast={toast} setModal={setModal} goMap={() => setTab(1)} />}
      {tab === 1 && <Mapping p={p} setProject={setProject} toast={toast} q={q} setQ={setQ} mapTab={mapTab} setMapTab={setMapTab} setModal={setModal} startWizard={startWizard} />}
      {tab === 2 && <Tpl p={p} setProject={setProject} toast={toast} setModal={setModal} />}
      {modal}
    </div>
  );
}

function kv(label, val, mono) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--t3)", marginBottom: 3 }}>{label}</div>
      <div className={mono ? "mono" : ""} style={{ fontSize: 13 }}>{val === "" || val == null ? "—" : val}</div>
    </div>
  );
}

function Basic({ p, setProject, toast, setModal, goMap }) {
  const c = p.cfg;
  async function save(group, fields) {
    const values = {};
    fields.forEach((f) => {
      const el = document.getElementById("cf_" + f);
      if (el) values[f] = el.type === "number" ? Number(el.value) : el.value;
    });
    try {
      const next = await api.patchCfg(p.pid, group, values);
      setProject(next);
      toast("已保存，变更写入系统日志", "单项编辑");
      setModal(null);
    } catch (e) {
      toast(e.message);
    }
  }
  function edit(title, group, fields) {
    setModal(
      <Modal title={"单项编辑 · " + title} onClose={() => setModal(null)}
        foot={<><button className="btn" onClick={() => setModal(null)}>取消</button><button className="btn primary" onClick={() => save(group, fields.map((f) => f[0]))}>保存</button></>}>
        <div className="grid g2">
          {fields.map((f) => (
            <Field key={f[0]} label={f[1]}>
              <input className={"input" + (f[2] === "mono" ? " mono" : "")} id={"cf_" + f[0]} type={f[2] === "num" ? "number" : "text"} defaultValue={f[0] === "appSecret" ? "" : c[f[0]] ?? ""} disabled={f[0] === "pid"} />
            </Field>
          ))}
        </div>
      </Modal>
    );
  }
  return (
    <>
      <div className="row mb12 wrap">
        <button className="btn sm" onClick={() => edit("基本信息", "basic", [["pid", "PID", "mono"], ["name", "项目名称"]])}>基本信息</button>
        <button className="btn sm" onClick={() => edit("采集参数", "collect", [["folder", "监控文件夹", "mono"], ["backup", "备份目录", "mono"], ["scan", "扫描间隔", "num"], ["stable", "稳定等待", "num"], ["offline", "离线判定", "num"], ["batch", "单批上限", "num"]])}>采集参数</button>
        <button className="btn sm" onClick={() => edit("推送与容错", "push", [["resendMax", "连续补发上限", "num"], ["retry", "重试次数", "num"], ["logClean", "日志清理天", "num"]])}>推送与容错</button>
        <button className="btn sm" onClick={() => edit("接口与鉴权", "api", [["appId", "AppID", "mono"], ["appSecret", "AppSecret"], ["tokenUrl", "Token URL", "mono"], ["pushUrl", "推送 URL", "mono"]])}>接口与鉴权</button>
        <button className="btn" onClick={async () => { try { const r = await api.testApi(p.pid); toast(r.detail, "连通测试"); } catch (e) { toast(e.message); } }}>测试接口连通</button>
        {p.ready ? <span className="tag ok">配置完整</span> : <span className="tag err">配置未完成</span>}
      </div>
      <div className="card mb12">
        <div className="sec-h">项目基本信息</div>
        <div className="grid g4">{kv("项目唯一 PID", c.pid, 1)}{kv("项目名称", c.name)}{kv("生产线／工序分区", `${(p.lines || []).length} 条 / ${countAreas(p)} 个`)}{kv("台车总数", (p.trolleys || []).length + " 台")}</div>
        <div className="row" style={{ marginTop: 11 }}>
          <span className="mini-note" style={{ flex: 1 }}>工序顺序由映射表拖拽决定。</span>
          <button className="btn sm" onClick={goMap}>去映射表调整顺序</button>
        </div>
      </div>
      <div className="card mb12">
        <div className="sec-h">采集参数</div>
        <div className="grid g3">{kv("RFID 监控文件夹", c.folder, 1)}{kv("JSON 备份目录", c.backup, 1)}{kv("扫描间隔", c.scan + " 秒")}{kv("文件稳定等待", c.stable + " 秒")}{kv("离线判定", c.offline + " 小时")}{kv("单批台车上限", c.batch)}</div>
      </div>
      <div className="card mb12">
        <div className="sec-h">推送与容错</div>
        <div className="grid g4">{kv("连续补发上限", c.resendMax)}{kv("接口失败重试次数", c.retry)}{kv("日志自动清理", c.logClean + " 天")}{kv("自动推送", p.push ? "运行中" : "已停止")}</div>
      </div>
      <div className="card">
        <div className="sec-h">客户平台接口与鉴权</div>
        <div className="grid g2">{kv("AppID", c.appId, 1)}{kv("AppSecret", c.appSecret ? "已保存（不回显）" : "未配置")}{kv("Token 获取接口", c.tokenUrl, 1)}{kv("客户上报接口", c.pushUrl, 1)}{kv("设备上报 Token", c.ingestTokenMasked, 1)}</div>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn sm" onClick={async () => {
            if (!confirm("重置后旧 Token 立即失效，读卡器需重新配置。")) return;
            try {
              const r = await api.resetToken(p.pid);
              toast("新 Token（仅显示一次）", r.ingestToken);
            } catch (e) { toast(e.message); }
          }}>重置上报 Token</button>
          <span className="mini-note">读卡器请求头携带 X-Ingest-Token。完整值仅在创建或重置时显示一次。</span>
        </div>
      </div>
    </>
  );
}

function Mapping({ p, setProject, toast, q, setQ, mapTab, setMapTab, setModal, startWizard }) {
  const qq = q.toLowerCase();
  async function saveLine(oldId, name, id) {
    try {
      setProject(await api.patchLine(p.pid, oldId, { name, id }));
      toast("已更新产线", "单条编辑");
      setModal(null);
    } catch (e) { toast(e.message); }
  }
  async function saveProc(lid, old, name, code) {
    try {
      setProject(await api.patchProc(p.pid, lid, old, { name, code }));
      toast("已更新工序", "单条编辑");
      setModal(null);
    } catch (e) { toast(e.message); }
  }
  function editLine(l) {
    setModal(
      <Modal title={"编辑产线 · " + l.name} onClose={() => setModal(null)}
        foot={<><button className="btn" onClick={() => setModal(null)}>取消</button><button className="btn primary" onClick={() => saveLine(l.id, document.getElementById("elName").value, document.getElementById("elId").value)}>保存</button></>}>
        <div className="grid g2">
          <Field label="产线名称"><input className="input" id="elName" defaultValue={l.name} /></Field>
          <Field label="产线 ID"><input className="input mono" id="elId" defaultValue={l.id} /></Field>
        </div>
      </Modal>
    );
  }
  function editProc(l, g) {
    setModal(
      <Modal title={"编辑工序 · " + g.name} onClose={() => setModal(null)}
        foot={<><button className="btn" onClick={() => setModal(null)}>取消</button><button className="btn primary" onClick={() => saveProc(l.id, g.code, document.getElementById("epName").value, document.getElementById("epCode").value)}>保存</button></>}>
        <div className="alert">顺序不在此填写，由上方卡片拖拽决定（当前第 {g.order} 位）。</div>
        <div className="grid g2">
          <Field label="工序名称"><input className="input" id="epName" defaultValue={g.name} /></Field>
          <Field label="工序编码"><input className="input mono" id="epCode" defaultValue={g.code} /></Field>
        </div>
      </Modal>
    );
  }
  async function move(l, i, d) {
    const j = i + d;
    if (j < 0 || j >= l.procs.length) return;
    const codes = l.procs.map((x) => x.code);
    const [g] = codes.splice(i, 1);
    codes.splice(j, 0, g);
    try {
      setProject(await api.orderProcs(p.pid, l.id, codes));
      toast("工序顺序已保存", "工序顺序");
    } catch (e) { toast(e.message); }
  }

  let rows = [];
  if (mapTab === 0) {
    (p.lines || []).forEach((l) => l.procs.forEach((g) => g.areas.forEach((a) => {
      const s = (l.id + l.name + g.code + g.name + a.id + a.name).toLowerCase();
      if (qq && !s.includes(qq)) return;
      rows.push(
        <tr key={l.id + g.code + a.id}>
          <td className="mono">{l.id}</td>
          <td>{l.name} <span className="pen" onClick={() => editLine(l)}>✎</span></td>
          <td className="mono">{g.code}</td>
          <td>{g.name} <span className="pen" onClick={() => editProc(l, g)}>✎</span></td>
          <td className="num">{g.order}</td>
          <td className="mono">{a.id}</td>
          <td>{a.name}</td>
          <td><span className={"tag" + ((a.nos || []).length === 3 ? " ok" : " warn")}>{(a.nos || []).length} 编号</span></td>
        </tr>
      );
    })));
  } else if (mapTab === 1) {
    (p.lines || []).forEach((l) => l.procs.forEach((g) => g.areas.forEach((a) => (a.nos || []).forEach((no, i) => {
      const eps = p.tagNos?.[no] || [];
      const s = (no + l.id + g.code + a.id + eps.join(" ")).toLowerCase();
      if (qq && !s.includes(qq)) return;
      rows.push(
        <tr key={no + a.id + i}>
          <td className="mono">{no}</td>
          <td className="mono">{eps.length ? eps.map((e, k) => <div key={k}>{e}</div>) : <span className="tag err">未定义</span>}</td>
          <td className="num">{eps.length}</td>
          <td className="mono">{l.id}</td>
          <td className="mono">{g.code}</td>
          <td className="mono">{a.id}</td>
          <td>{a.name}</td>
          <td className="num">{i + 1}/{(a.nos || []).length}</td>
        </tr>
      );
    }))));
  } else {
    (p.trolleys || []).forEach((t, i) => {
      const s = (t.tz + t.name + t.reader).toLowerCase();
      if (qq && !s.includes(qq)) return;
      rows.push(
        <tr key={t.tz}>
          <td className="num">{i + 1}</td>
          <td className="mono">{t.tz}</td>
          <td>{t.name}</td>
          <td className="mono">{t.reader}</td>
          <td>
            <button className="btn sm" onClick={() => {
              setModal(
                <Modal title={"编辑台车 · " + t.name} onClose={() => setModal(null)}
                  foot={<><button className="btn" onClick={() => setModal(null)}>取消</button>
                    <button className="btn primary" onClick={async () => {
                      try {
                        setProject(await api.patchTrolley(p.pid, t.tz, { tz: document.getElementById("etTz").value, name: document.getElementById("etName").value, reader: document.getElementById("etReader").value }));
                        toast("已更新台车", "单条编辑"); setModal(null);
                      } catch (e) { toast(e.message); }
                    }}>保存</button></>}>
                  <div className="grid g2">
                    <Field label="台车 ID"><input className="input mono" id="etTz" defaultValue={t.tz} /></Field>
                    <Field label="台座名称"><input className="input" id="etName" defaultValue={t.name} /></Field>
                  </div>
                  <Field label="读卡器设备 ID"><input className="input mono" id="etReader" defaultValue={t.reader} /></Field>
                </Modal>
              );
            }}>编辑</button>
          </td>
        </tr>
      );
    });
  }

  return (
    <>
      <div className="tabs">
        {["产线·工序·区域", "标签编号 · EPC 映射", "读卡器·台车"].map((t, i) => (
          <button key={t} className={"tab" + (mapTab === i ? " on" : "")} onClick={() => setMapTab(i)}>
            {t}<span className="cnt">{[`${(p.lines || []).length} / ${countProcs(p)} / ${countAreas(p)}`, `${countNos(p)} / ${countEpcs(p)}`, (p.trolleys || []).length][i]}</span>
          </button>
        ))}
      </div>
      <div className="toolbar">
        <input className="search" placeholder="按编码 / 名称 / EPC 模糊搜索" value={q} onChange={(e) => setQ(e.target.value)} />
        <div style={{ flex: 1 }} />
        <button className="btn" onClick={() => startWizard("edit", p, 2)}>文件导入</button>
        <button className="btn" onClick={() => downloadAuth(api.exportUrl(p.pid, mapTab === 2 ? "tz" : mapTab === 1 ? "no" : "pos"), mapTab === 2 ? "港台座编号映射.xlsx" : mapTab === 1 ? "编号-映射表.xlsx" : "位置-编号映射表.xlsx")}>导出 Excel</button>
        <button className="btn" onClick={() => startWizard("edit", p, 2)}>整体流程编辑</button>
      </div>
      {mapTab === 0 && (
        <div className="card mb12">
          <div className="sec-h">产线 · 工序（拖拽定义顺序）</div>
          <p className="sec-t">每条产线工序从左到右即为顺序，↑ ↓ 即时保存。</p>
          {(p.lines || []).map((l) => (
            <div className="row wrap" style={{ marginBottom: 9 }} key={l.id}>
              <span className="note" style={{ minWidth: 118 }}>{l.name} <span className="pen" onClick={() => editLine(l)}>✎</span></span>
              {l.procs.map((g, i) => (
                <span key={g.code}>
                  <span className="ordchip">
                    <b>{i + 1}</b> {g.name} <span className="mono">{g.code}</span>
                    <button className="btn sm" onClick={() => editProc(l, g)}>✎</button>
                    <button className="btn sm" onClick={() => move(l, i, -1)}>↑</button>
                    <button className="btn sm" onClick={() => move(l, i, 1)}>↓</button>
                  </span>
                  {i < l.procs.length - 1 ? <span style={{ color: "var(--t3)", margin: "0 6px" }}>→</span> : null}
                </span>
              ))}
            </div>
          ))}
        </div>
      )}
      {mapTab === 1 && <div className="alert">两级映射：位置 → 蓝牙标签编号 → 多个 EPC。读到任一 EPC 即可定位。</div>}
      <div className="card pad0">
        <div className="tb-wrap">
          <table className="tb">
            <thead>
              {mapTab === 0 && <tr><th>产线 ID</th><th>产线名称</th><th>工序编码</th><th>工序名称</th><th className="num">顺序</th><th>区域 ID</th><th>区域名称</th><th>标签数</th></tr>}
              {mapTab === 1 && <tr><th>标签编号</th><th>EPC 实际值</th><th className="num">EPC 数</th><th>产线 ID</th><th>工序编码</th><th>区域 ID</th><th>区域名称</th><th className="num">序位</th></tr>}
              {mapTab === 2 && <tr><th>序号</th><th>台车 ID</th><th>台座名称</th><th>读卡器设备 ID</th><th></th></tr>}
            </thead>
            <tbody>{rows}</tbody>
          </table>
          {!rows.length && <div className="empty">无匹配数据，请先导入或手工填写映射表。</div>}
        </div>
      </div>
    </>
  );
}

function Tpl({ p, setProject, toast, setModal }) {
  const t = p.tpl;
  function open() {
    setModal(
      <Modal title="单项编辑 · 报文模板" onClose={() => setModal(null)}
        foot={<><button className="btn" onClick={() => setModal(null)}>取消</button>
          <button className="btn primary" onClick={async () => {
            const fields = [...document.querySelectorAll("#tplChips .fchip.on")].map((e) => e.getAttribute("data-k"));
            try {
              setProject(await api.patchTpl(p.pid, { fields, json: document.getElementById("tplJson").value }));
              toast("模板已保存", "报文模板"); setModal(null);
            } catch (e) { toast(e.message); }
          }}>保存</button></>}>
        <div className="fchips" id="tplChips">
          {ALL_FIELDS.map((k) => (
            <span key={k} className={"fchip" + (t.fields.includes(k) ? " on" : "")} data-k={k} onClick={(e) => e.currentTarget.classList.toggle("on")}>{k}</span>
          ))}
        </div>
        <Field label="报文模板 JSON"><textarea className="input mono" id="tplJson" rows="9" defaultValue={t.json} /></Field>
      </Modal>
    );
  }
  return (
    <>
      <div className="row mb12">
        <span className={"tag" + (t.custom ? " info" : " ok")}>{t.custom ? "自定义模板" : "默认模板"}</span>
        <button className="btn sm" onClick={open}>单项编辑（字段 / 报文）</button>
      </div>
      <div className="card mb12">
        <div className="sec-h">已勾选字段</div>
        <div className="fchips">{t.fields.map((k) => <span key={k} className="fchip on">{k}</span>)}</div>
      </div>
      <div className="card">
        <div className="sec-h">推送报文</div>
        <pre className="mono" style={{ margin: 0, background: "var(--paper-alt)", border: "1px solid var(--line)", borderRadius: 8, padding: 14, overflow: "auto" }}>{t.json}</pre>
      </div>
    </>
  );
}
