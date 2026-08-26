import { useCallback, useEffect, useState } from "react";
import { api, downloadAuth } from "../api";
import { Modal } from "../ui";

const CLS = { 信息: "info", 成功: "ok", 失败: "err" };

export default function Logs({ project, toast }) {
  const [q, setQ] = useState("");
  const [data, setData] = useState({ items: [], stats: { total: 0, ok: 0, fail: 0, logClean: 30 } });
  const [modal, setModal] = useState(null);
  const pid = project.pid;

  const load = useCallback(async () => {
    setData(await api.logs(pid, q));
  }, [pid, q]);

  useEffect(() => {
    load().catch((e) => toast(e.message));
    const t = setInterval(() => load().catch(() => {}), 4000);
    return () => clearInterval(t);
  }, [load, toast]);

  const s = data.stats || {};
  const rows = data.items || [];

  return (
    <div>
      <div className="kicker">模块 G · 项目内部</div>
      <h1 className="page-title">系统日志</h1>
      <p className="sec-sub">推送 / 运行日志：手动标位、批量补推、单条推送、映射表整体编辑等操作均写入日志；按项目「日志自动清理（{s.logClean || 30} 天）」定期清理。</p>
      <div className="statbar" style={{ gridTemplateColumns: "repeat(3,1fr)" }}>
        <div className="stat"><div className="n">{s.total || 0}</div><div className="l">日志条数</div></div>
        <div className="stat"><div className="n ok">{s.ok || 0}</div><div className="l">信息 / 成功</div></div>
        <div className="stat"><div className="n" style={{ color: "var(--danger)" }}>{s.fail || 0}</div><div className="l">失败</div></div>
      </div>
      <div className="toolbar">
        <input className="search" placeholder="按类型 / 内容搜索" value={q} onChange={(e) => setQ(e.target.value)} />
        <div style={{ flex: 1 }} />
        <button className="btn sm" onClick={() => downloadAuth(api.exportLogsUrl(pid, q), `${pid}-system-logs.xlsx`).then(() => toast("已导出当前筛选结果 system-logs.xlsx")).catch((e) => toast(e.message))}>⤒ 导出 Excel</button>
        <button className="btn sm danger" onClick={() => setModal(
          <Modal title="清空系统日志" onClose={() => setModal(null)}
            foot={
              <>
                <button className="btn" onClick={() => setModal(null)}>取消</button>
                <button className="btn danger" onClick={async () => {
                  try {
                    await api.clearLogs(pid);
                    setModal(null);
                    await load();
                    toast("系统日志已清空（不可恢复）");
                  } catch (e) { toast(e.message); }
                }}>确认清空</button>
              </>
            }>
            <div className="alert err">清空后<b>不可恢复</b>，确认继续？</div>
          </Modal>
        )}>清空日志</button>
      </div>
      <div className="card pad0">
        <div className="tb-wrap" style={{ maxHeight: 460 }}>
          <table className="tb">
            <thead><tr><th className="num">序号</th><th>时间</th><th>类型</th><th>内容</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="num">{r.no}</td>
                  <td className="mono">{r.time}</td>
                  <td><span className={"tag " + (CLS[r.type] || "info")}>{r.type}</span></td>
                  <td>{r.msg}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length ? <div className="empty">无匹配日志。</div> : null}
        </div>
      </div>
      {modal}
    </div>
  );
}
