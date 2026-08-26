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
  appId: "",
  appSecret: "",
  tokenUrl: "",
  pushUrl: "",
};
export const DEFAULT_FIELDS = [
  "areaCode",
  "beamAssetsCode",
  "beamLineCode",
  "identifyTime",
  "procedureCode",
  "sportState",
];
export const ALL_FIELDS = DEFAULT_FIELDS.concat(["deviceCode", "projectCode"]);
export const DEFAULT_TPL = `{
  "areaCode": "\${areaCode}",
  "beamAssetsCode": "\${beamAssetsCode}",
  "beamLineCode": "\${beamLineCode}",
  "identifyTime": "\${identifyTime}",
  "procedureCode": "\${procedureCode}",
  "sportState": "\${sportState}"
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
        <circle cx="12" cy="12" r="10" fill="none" stroke="#00A870" strokeWidth="2" />
        <path d="M7 12.6l3.2 3.2L17 9" fill="none" stroke="#00A870" strokeWidth="2" strokeLinecap="round" />
      </svg>
      Omada {sub ? <span className="sub">{sub}</span> : null}
    </div>
  );
}
