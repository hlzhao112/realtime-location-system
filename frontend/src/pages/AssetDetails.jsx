import { useEffect, useState } from "react";
import { api, downloadAuth } from "../api";

export default function AssetDetails({ pid, toast, onClose }) {
  const [tab, setTab] = useState("trolleys");
  const [data, setData] = useState({ trolleys: [], tags: [], inactiveDays: 30 });

  useEffect(() => {
    api.assets(pid).then(setData).catch((e) => toast(e.message));
  }, [pid, toast]);

  const rows = tab === "trolleys" ? data.trolleys : data.tags;

  return (
    <section>
      <div className="row between">
        <h2 className="page-title" style={{ fontSize: 20 }}>台座 / 标签详情</h2>
        <button className="btn sm" onClick={onClose}>关闭</button>
      </div>
      <p className="sec-sub">超过 {data.inactiveDays || 30} 天未上报或未读取会标为异常。无记录显示「从未上报 / 从未读取」，不会伪造时间。</p>
      <div className="toolbar">
        <button className={"btn sm" + (tab === "trolleys" ? " primary" : "")} onClick={() => setTab("trolleys")}>台座列表</button>
        <button className={"btn sm" + (tab === "tags" ? " primary" : "")} onClick={() => setTab("tags")}>标签列表</button>
        <div style={{ flex: 1 }} />
        <button
          className="btn sm"
          onClick={() =>
            downloadAuth(
              tab === "trolleys" ? api.exportAssetTrolleysUrl(pid) : api.exportAssetTagsUrl(pid),
              tab === "trolleys" ? `${pid}-trolleys.xlsx` : `${pid}-tags.xlsx`
            ).then(() => toast("已导出 Excel")).catch((e) => toast(e.message))
          }
        >
          ⤒ 导出 Excel
        </button>
      </div>
      <div className="tb-wrap" style={{ maxHeight: "70vh" }}>
        {tab === "trolleys" ? (
          <table className="tb">
            <thead>
              <tr>
                <th>台车编号</th>
                <th>台座名称</th>
                <th>读卡器 ID</th>
                <th>最近上报时间</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.tz} className={r.abnormal || r.never ? "asset-dim" : undefined}>
                  <td className="mono">{r.tz}</td>
                  <td>{r.name}</td>
                  <td className="mono">{r.reader}</td>
                  <td className="mono">{r.last}</td>
                  <td>{r.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <table className="tb" style={{ tableLayout: "fixed" }}>
            <thead>
              <tr>
                <th style={{ width: 110 }}>标签编号</th>
                <th style={{ width: 220 }}>EPC</th>
                <th>产线 / 工序 / 区域</th>
                <th style={{ width: 180 }}>最近读取时间</th>
                <th style={{ width: "20%" }}>Hex</th>
                <th style={{ width: 120 }}>状态</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.tagNo + r.epc + i} className={r.abnormal || r.never ? "asset-dim" : undefined}>
                  <td className="mono">{r.tagNo}</td>
                  <td className="mono epc-plain">{r.epc}</td>
                  <td>{r.line} · {r.proc} · {r.area}</td>
                  <td className="mono">{r.last}</td>
                  <td className="mono hex-col">{r.hex}</td>
                  <td>{r.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!rows.length ? <div className="empty">暂无数据。</div> : null}
      </div>
    </section>
  );
}
