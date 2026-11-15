export const DEFAULT_CFG = {
  pid: "",
  name: "",
  folder: "D:\\RFID\\watch",
  backup: "D:\\RFID\\backup",
  scan: 5,
  stable: 2,
  offline: 0.5,
  batch: 20,
  resendMax: 10,
  retry: 3,
  logClean: 30,
  keepSec: 600,
  areaSingle: true,
  rawKeepMonth: 1,
  pushKeepMonth: 1,
  hexAggMin: 5,
  inactiveDays: 30,
  appId: "",
  appSecret: "",
  tokenUrl: "",
  pushUrl: "",
};
export const DEFAULT_FIELDS = [
  "dataSource",
  "procedureCode",
  "beamAssetsCode",
  "beamLineCode",
  "identifyTime",
  "sportState",
  "areaCode",
  "projectId",
  "serverUrl",
  "dataIndex",
  "isAutoImported",
];
export const ALL_FIELDS = DEFAULT_FIELDS.slice();
export const FIELD_TPL_VALUE = {
  dataSource: "1",
  procedureCode: '"${procedureCode}"',
  beamAssetsCode: '"${beamAssetsCode}"',
  beamLineCode: '"${beamLineCode}"',
  identifyTime: '"${identifyTime}"',
  sportState: '"${sportState}"',
  areaCode: '"${areaCode}"',
  projectId: '"${projectId}"',
  serverUrl: '"${serverUrl}"',
  dataIndex: "0",
  isAutoImported: "true",
};
export const DEFAULT_TPL = `{
  "dataSource": 1,
  "procedureCode": "\${procedureCode}",
  "beamAssetsCode": "\${beamAssetsCode}",
  "beamLineCode": "\${beamLineCode}",
  "identifyTime": "\${identifyTime}",
  "sportState": "\${sportState}",
  "areaCode": "\${areaCode}",
  "projectId": "\${projectId}",
  "serverUrl": "\${serverUrl}",
  "dataIndex": 0,
  "isAutoImported": true
}`;

export function blankProject() {
  return {
    pid: "",
    name: "",
    cfg: { ...DEFAULT_CFG },
    lines: [],
    trolleys: [],
    tagNos: {},
    tpl: { fields: DEFAULT_FIELDS.slice(), json: DEFAULT_TPL, custom: false },
    push: false,
    ready: false,
  };
}

export function countAreas(p) {
  let n = 0;
  (p.lines || []).forEach((l) => l.procs.forEach((g) => (n += g.areas.length)));
  return n;
}
export function countProcs(p) {
  let n = 0;
  (p.lines || []).forEach((l) => (n += l.procs.length));
  return n;
}
export function countNos(p) {
  let n = 0;
  (p.lines || []).forEach((l) => l.procs.forEach((g) => g.areas.forEach((a) => (n += (a.nos || []).length))));
  return n;
}
export function countEpcs(p) {
  const tag = p.tagNos || {};
  let n = 0;
  (p.lines || []).forEach((l) =>
    l.procs.forEach((g) =>
      g.areas.forEach((a) => (a.nos || []).forEach((no) => (n += (tag[no] || a.epcs || []).length)))
    )
  );
  return n;
}
export function formatStay(mins) {
  if (mins == null || mins === "—") return "—";
  const n = Math.max(0, Number(mins));
  if (!Number.isFinite(n) || n < 1) return "不足 1 分钟";
  const year = 365 * 24 * 60;
  const month = 30 * 24 * 60;
  const day = 24 * 60;
  const hour = 60;
  let left = Math.floor(n);
  const y = Math.floor(left / year); left %= year;
  const mo = Math.floor(left / month); left %= month;
  const d = Math.floor(left / day); left %= day;
  const h = Math.floor(left / hour); left %= hour;
  const m = left;
  const parts = [];
  if (y) parts.push(`${y} 年`);
  if (mo) parts.push(`${mo} 个月`);
  if (d) parts.push(`${d} 天`);
  if (h) parts.push(`${h} 小时`);
  if (m) parts.push(`${m} 分钟`);
  return parts.join("") || "不足 1 分钟";
}

export function step1Done(p) {
  const c = p.cfg || {};
  return !!(c.pid && c.name && c.folder);
}
export function step2Done(p) {
  return countAreas(p) > 0 && countEpcs(p) > 0 && (p.trolleys || []).length > 0;
}

export function toSaveBody(p) {
  const c = p.cfg || {};
  return {
    pid: c.pid,
    name: c.name,
    folder: c.folder,
    backup: c.backup,
    scan: Number(c.scan) || 5,
    stable: Number(c.stable) || 2,
    offline: Number(c.offline) || 0.5,
    batch: Number(c.batch) || 20,
    resendMax: Number(c.resendMax) || 10,
    retry: Number(c.retry) || 3,
    logClean: Number(c.logClean) || 30,
    keepSec: Number(c.keepSec) || 600,
    areaSingle: c.areaSingle !== false,
    rawKeepMonth: Number(c.rawKeepMonth) || 1,
    pushKeepMonth: Number(c.pushKeepMonth) || 1,
    hexAggMin: Number(c.hexAggMin) || 0,
    inactiveDays: Number(c.inactiveDays) || 30,
    appId: c.appId || "",
    appSecret: c.appSecret || "",
    tokenUrl: c.tokenUrl || "",
    pushUrl: c.pushUrl || "",
    lines: p.lines || [],
    trolleys: p.trolleys || [],
    tagNos: p.tagNos || {},
    tpl: p.tpl,
    push: p.push || false,
  };
}

export function fromApi(p) {
  return {
    ...p,
    cfg: p.cfg,
    lines: p.lines || [],
    trolleys: p.trolleys || [],
    tagNos: p.tagNos || {},
    tpl: p.tpl,
  };
}

export function Logo({ sub }) {
  return (
    <div className="brand-logo">
      <svg className="mark" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 21s7-6.2 7-11.2A7 7 0 0 0 5 9.8C5 14.8 12 21 12 21z" fill="none" stroke="#00A870" strokeWidth="2" />
        <circle cx="12" cy="9.8" r="2.2" fill="none" stroke="#00A870" strokeWidth="2" />
      </svg>
      预制梁台车追踪{sub ? <span className="sub">{sub}</span> : null}
    </div>
  );
}
