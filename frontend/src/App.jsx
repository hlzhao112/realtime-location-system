import { useCallback, useEffect, useState } from "react";
import { api, getToken, setToken } from "./api";
import { Logo, blankProject, fromApi } from "./helpers.jsx";
import Board from "./pages/Board.jsx";
import Info from "./pages/Info.jsx";
import Logs from "./pages/Logs.jsx";
import Raw from "./pages/Raw.jsx";
import Wizard from "./pages/Wizard.jsx";
import { Modal, ToastHost } from "./ui.jsx";

const NAV = [
  { id: "board", label: "主视图 · 实时监控 + 推送记录", tail: "E" },
  { id: "info", label: "项目信息", tail: "C·D" },
  { id: "raw", label: "原始数据监控", tail: "B" },
  { id: "log", label: "系统日志", tail: "G" },
];

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  const [view, setView] = useState("projects");
  const [list, setList] = useState([]);
  const [stats, setStats] = useState({ total: 0, running: 0, lines: 0, trolleys: 0 });
  const [cur, setCur] = useState(null);
  const [wiz, setWiz] = useState(null);
  const [q, setQ] = useState("");
  const [toasts, setToasts] = useState([]);
  const [modal, setModal] = useState(null);
  const [user, setUser] = useState("admin");

  const toast = useCallback((msg, k) => {
    const id = Date.now() + Math.random();
    setToasts((t) => t.concat([{ id, msg, k }]));
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3600);
  }, []);

  const loadList = useCallback(async () => {
    const data = await api.projects();
    setList(data.items);
    setStats(data.stats);
  }, []);

  useEffect(() => {
    if (!authed) return;
    api.me().then((u) => setUser(u.username)).catch(() => {
      setToken("");
      setAuthed(false);
    });
    loadList().catch((e) => toast(e.message));
  }, [authed, loadList, toast]);

  function startWizard(mode, src, step) {
    const draft = mode === "new" ? blankProject() : fromApi(JSON.parse(JSON.stringify(src)));
    if (mode === "edit") {
      draft._lockedPid = true;
      draft.cfg = { ...draft.cfg, pid: src.pid, name: src.name };
    }
    setWiz({ mode, step: step || 1, draft });
    setView("wizard");
  }

  async function enter(pid) {
    const p = await api.project(pid);
    setCur(fromApi(p));
    setView("board");
  }

  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "g" || e.key === "G") {
        setCur(null);
        setView("projects");
        loadList();
        return;
      }
      if (cur && "1234".includes(e.key)) setView(["board", "info", "raw", "log"][Number(e.key) - 1]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cur, loadList]);

  if (!authed) {
    return (
      <>
        <Login onOk={(name) => { setUser(name); setAuthed(true); }} toast={toast} />
        <ToastHost toasts={toasts} />
      </>
    );
  }

  const filtered = list.filter((p) => !q || p.pid.toLowerCase().includes(q.toLowerCase()) || p.name.toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      <div id="app" className="on">
        <div className="appbar">
          <Logo />
          <div className="crumb">
            <span className="go" onClick={() => { setCur(null); setView("projects"); loadList(); }}>项目看板</span>
            {cur && <><span className="sep">/</span><b>{cur.name}</b><span className="tag mono">{cur.pid}</span></>}
            {view === "wizard" && <><span className="sep">/</span><b>{wiz?.mode === "edit" ? "编辑项目" : wiz?.mode === "copy" ? "复制项目" : "新建项目"}</b></>}
            {cur && view !== "wizard" && <><span className="sep">/</span><span>{NAV.find((x) => x.id === view)?.label.split(" · ")[0] || "项目信息"}</span></>}
          </div>
          <div className="spacer" />
          <span className="pill admin">管理员 · {user}</span>
          <button className="btn sm" onClick={() => {
            setModal(
              <Modal title="修改密码" onClose={() => setModal(null)}
                foot={<><button className="btn" onClick={() => setModal(null)}>取消</button>
                  <button className="btn primary" onClick={async () => {
                    try {
                      await api.password(document.getElementById("op").value, document.getElementById("np").value);
                      toast("密码已更新"); setModal(null);
                    } catch (e) { toast(e.message); }
                  }}>保存</button></>}>
                <div className="field"><label>旧密码</label><input className="input" id="op" type="password" /></div>
                <div className="field"><label>新密码</label><input className="input" id="np" type="password" /></div>
              </Modal>
            );
          }}>修改密码</button>
          <button className="btn sm" onClick={() => { setToken(""); setAuthed(false); setCur(null); }}>退出</button>
        </div>
        <nav className="sidebar">
          <div className="nav-group">工作区</div>
          <button className={"nav-item" + (view === "projects" && !cur ? " on" : "")} onClick={() => { setCur(null); setView("projects"); loadList(); }}>
            <span className="dot" />项目看板<span className="tail">{list.length}</span>
          </button>
          {cur && (
            <>
              <div className="nav-group">{cur.name}</div>
              {NAV.map((m) => (
                <button key={m.id} className={"nav-item" + (view === m.id ? " on" : "") + (m.reserved ? " mut" : "")} onClick={() => setView(m.id)}>
                  <span className="dot" />{m.label}<span className="tail">{m.tail}</span>
                </button>
              ))}
            </>
          )}
        </nav>
        <main className="main">
          {view === "projects" && (
            <section>
              <div className="kicker">模块 C · 首屏</div>
              <h1 className="page-title">项目看板</h1>
              <p className="sec-sub">卡片式展示全部项目。进入项目默认落在实时监控看板；编辑项目可回到「基本配置 + 映射表」向导。</p>
              <div className="statbar">
                <div className="stat"><div className="n">{stats.total}</div><div className="l">项目总数</div></div>
                <div className="stat"><div className="n ok">{stats.running}</div><div className="l">推送运行中</div></div>
                <div className="stat"><div className="n info">{stats.lines}</div><div className="l">生产线总数</div></div>
                <div className="stat"><div className="n mut">{stats.trolleys}</div><div className="l">台车总数</div></div>
              </div>
              <div className="toolbar">
                <input className="search" placeholder="搜索 PID / 项目名称" value={q} onChange={(e) => setQ(e.target.value)} />
                <div style={{ flex: 1 }} />
                <button className="btn primary" onClick={() => startWizard("new")}>新建项目</button>
              </div>
              <div className="proj-grid">
                {filtered.map((p) => (
                  <div className="proj-card" key={p.pid}>
                    <div className="row between">
                      <span className="pid">{p.pid}</span>
                      {p.ready ? (p.push ? <span className="tag ok">推送运行中</span> : <span className="tag warn">推送已停止</span>) : <span className="tag err">配置未完成</span>}
                    </div>
                    <h3>{p.name}</h3>
                    <div className="kv">
                      <div>生产线 <b>{p.lines}</b></div><div>工序分区 <b>{p.areas}</b></div>
                      <div>台车 <b>{p.trolleys}</b></div><div>在线台车 <b>{p.online}</b></div>
                      <div style={{ gridColumn: "span 2" }}>最后位置更新 <b>{p.lastPush}</b></div>
                    </div>
                    {!p.ready && <div className="alert warn" style={{ margin: "14px 0 0" }}>Step1／Step2 未完成，项目不可启用监测与推送。</div>}
                    <div className="acts">
                      <button className="btn sm" onClick={() => enter(p.pid)}>进入项目</button>
                      <button className="btn sm" onClick={async () => startWizard("edit", await api.project(p.pid))}>编辑</button>
                      <button className="btn sm" onClick={() => {
                        setModal(
                          <Modal title={"复制项目 · " + p.name} onClose={() => setModal(null)}
                            foot={<><button className="btn" onClick={() => setModal(null)}>取消</button>
                              <button className="btn primary" onClick={async () => {
                                try {
                                  const created = await api.copyProject(p.pid, { pid: document.getElementById("cpPid").value.trim(), name: document.getElementById("cpName").value.trim() });
                                  setModal(null);
                                  toast("已复制，凭据需重新填写", "复制");
                                  startWizard("edit", created);
                                } catch (e) { toast(e.message); }
                              }}>复制并进入向导</button></>}>
                            <div className="alert">复制配置与映射，不复制运行数据。AppSecret 不复制。</div>
                            <div className="field"><label>新项目 PID</label><input className="input" id="cpPid" defaultValue={p.pid + "_2"} /></div>
                            <div className="field"><label>项目名称</label><input className="input" id="cpName" defaultValue={p.name + "-副本"} /></div>
                          </Modal>
                        );
                      }}>复制</button>
                      <button className="btn sm danger" onClick={() => {
                        setModal(
                          <Modal title={"删除项目 · " + p.name} onClose={() => setModal(null)}
                            foot={<><button className="btn" onClick={() => setModal(null)}>取消</button>
                              <button className="btn danger" onClick={async () => {
                                if (document.getElementById("delPid").value.trim() !== p.pid) return toast("PID 不匹配，未删除");
                                try { await api.deleteProject(p.pid); setModal(null); loadList(); toast("项目已删除", "删除"); } catch (e) { toast(e.message); }
                              }}>确认删除</button></>}>
                            <div className="alert err">删除后不可恢复，映射与运行数据一并删除。</div>
                            <div className="field"><label>请输入 PID {p.pid} 以确认</label><input className="input" id="delPid" /></div>
                          </Modal>
                        );
                      }}>删除</button>
                    </div>
                  </div>
                ))}
                <div className="proj-card new" onClick={() => startWizard("new")}>
                  <div className="plus">+</div>
                  <div>新建项目</div>
                  <div className="mini-note">三步向导 · 前两步必填</div>
                </div>
              </div>
            </section>
          )}
          {view === "wizard" && wiz && (
            <Wizard
              draft={wiz.draft}
              setDraft={(d) => setWiz({ ...wiz, draft: d })}
              mode={wiz.mode}
              step={wiz.step}
              setStep={(n) => setWiz({ ...wiz, step: n })}
              onCancel={() => { if (cur) setView("board"); else { setView("projects"); loadList(); } }}
              onSaved={(p) => { setCur(fromApi(p)); setWiz(null); setView("board"); loadList(); }}
              toast={toast}
            />
          )}
          {view === "info" && cur && (
            <Info project={cur} setProject={(p) => setCur(fromApi(p))} toast={toast} startWizard={startWizard} />
          )}
          {view === "board" && cur && (
            <Board project={cur} toast={toast} startWizard={startWizard} />
          )}
          {view === "raw" && cur && <Raw project={cur} toast={toast} />}
          {view === "log" && cur && <Logs project={cur} toast={toast} />}
        </main>
      </div>
      {modal}
      <ToastHost toasts={toasts} />
    </>
  );
}

function Login({ onOk, toast }) {
  const [u, setU] = useState("admin");
  const [p, setP] = useState("omada2026");
  const [busy, setBusy] = useState(false);
  async function submit() {
    if (!u || !p) return toast("请填写账号和密码");
    setBusy(true);
    try {
      const r = await api.login(u, p);
      setToken(r.access_token);
      onOk(r.username);
    } catch (e) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div id="login">
      <div className="login-card">
        <Logo sub="Trolley Tracking" />
        <h1>预制梁台车追踪与数据推送系统</h1>
        <p>管理员登录 · 首版仅管理员角色</p>
        <div className="field"><label>账号<span className="req">*</span></label><input className="input" value={u} onChange={(e) => setU(e.target.value)} /></div>
        <div className="field"><label>密码<span className="req">*</span></label><input className="input" type="password" value={p} onChange={(e) => setP(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} /></div>
        <button className="btn primary block" disabled={busy} onClick={submit}>登录</button>
        <div className="mini-note" style={{ marginTop: 12 }}>默认账号 admin / omada2026，登录后进入项目看板。</div>
      </div>
    </div>
  );
}
