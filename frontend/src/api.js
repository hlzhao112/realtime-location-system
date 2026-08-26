const TOKEN_KEY = "omada_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body && !(opts.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) {
    setToken("");
    throw new Error("未登录或会话已过期");
  }
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    const data = await res.json();
    if (!res.ok) {
      const msg = data.detail || data.message || res.statusText;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  }
  if (!res.ok) throw new Error(await res.text());
  return res;
}

export const api = {
  login: (username, password) =>
    request("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  me: () => request("/api/v1/auth/me"),
  password: (old_password, new_password) =>
    request("/api/v1/auth/password", { method: "POST", body: JSON.stringify({ old_password, new_password }) }),
  projects: () => request("/api/v1/projects"),
  project: (pid) => request(`/api/v1/projects/${pid}`),
  createProject: (body) => request("/api/v1/projects", { method: "POST", body: JSON.stringify(body) }),
  updateProject: (pid, body) => request(`/api/v1/projects/${pid}`, { method: "PUT", body: JSON.stringify(body) }),
  copyProject: (pid, body) => request(`/api/v1/projects/${pid}/copy`, { method: "POST", body: JSON.stringify(body) }),
  deleteProject: (pid) => request(`/api/v1/projects/${pid}`, { method: "DELETE" }),
  patchCfg: (pid, group, values) =>
    request(`/api/v1/projects/${pid}/cfg`, { method: "PATCH", body: JSON.stringify({ group, values }) }),
  patchTpl: (pid, body) => request(`/api/v1/projects/${pid}/template`, { method: "PATCH", body: JSON.stringify(body) }),
  patchLine: (pid, code, body) =>
    request(`/api/v1/projects/${pid}/lines/${encodeURIComponent(code)}`, { method: "PATCH", body: JSON.stringify(body) }),
  patchProc: (pid, line, code, body) =>
    request(`/api/v1/projects/${pid}/lines/${encodeURIComponent(line)}/procedures/${encodeURIComponent(code)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  orderProcs: (pid, line, codes) =>
    request(`/api/v1/projects/${pid}/lines/${encodeURIComponent(line)}/procedures/order`, {
      method: "PUT",
      body: JSON.stringify({ codes }),
    }),
  patchArea: (pid, line, proc, area, body) =>
    request(
      `/api/v1/projects/${pid}/areas/${encodeURIComponent(line)}/${encodeURIComponent(proc)}/${encodeURIComponent(area)}`,
      { method: "PATCH", body: JSON.stringify(body) }
    ),
  patchTrolley: (pid, tz, body) =>
    request(`/api/v1/projects/${pid}/trolleys/${encodeURIComponent(tz)}`, { method: "PATCH", body: JSON.stringify(body) }),
  testApi: (pid) => request(`/api/v1/projects/${pid}/test-api`, { method: "POST" }),
  resetToken: (pid) => request(`/api/v1/projects/${pid}/token/reset`, { method: "POST" }),
  previewTagsDraft: async (posFile, noFile) => {
    const fd = new FormData();
    fd.append("pos", posFile);
    fd.append("no", noFile);
    return request("/api/v1/imports/tags/preview", { method: "POST", body: fd });
  },
  previewTags: async (pid, posFile, noFile) => {
    const fd = new FormData();
    fd.append("pos", posFile);
    fd.append("no", noFile);
    return request(`/api/v1/projects/${pid}/imports/tags/preview`, { method: "POST", body: fd });
  },
  commitTags: (pid, payload) =>
    request(`/api/v1/projects/${pid}/imports/tags/commit`, { method: "POST", body: JSON.stringify(payload) }),
  previewTzDraft: async (file) => {
    const fd = new FormData();
    fd.append("file", file);
    return request("/api/v1/imports/trolleys/preview", { method: "POST", body: fd });
  },
  previewTz: async (pid, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return request(`/api/v1/projects/${pid}/imports/trolleys/preview`, { method: "POST", body: fd });
  },
  commitTz: (pid, trolleys) =>
    request(`/api/v1/projects/${pid}/imports/trolleys/commit`, { method: "POST", body: JSON.stringify({ trolleys }) }),
  templateUrl: (kind) => `/api/v1/templates/${kind}`,
  exportUrl: (pid, kind) => `/api/v1/projects/${pid}/exports/${kind}`,
  board: (pid) => request(`/api/v1/projects/${pid}/board`),
  runtime: (pid, body) => request(`/api/v1/projects/${pid}/runtime`, { method: "PATCH", body: JSON.stringify(body) }),
  placeTrolley: (pid, tz, body) =>
    request(`/api/v1/projects/${pid}/trolleys/${encodeURIComponent(tz)}/place`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  pushes: (pid, q = "", merge = false) =>
    request(`/api/v1/projects/${pid}/pushes?q=${encodeURIComponent(q)}&merge=${merge ? "true" : "false"}`),
  pushPayload: (pid, id) => request(`/api/v1/projects/${pid}/pushes/${id}/payload`),
  pushOne: (pid, id) => request(`/api/v1/projects/${pid}/pushes/${id}/push`, { method: "POST" }),
  editPush: (pid, id, body) =>
    request(`/api/v1/projects/${pid}/pushes/${id}/edit`, { method: "POST", body: JSON.stringify(body) }),
  batchPush: (pid) => request(`/api/v1/projects/${pid}/pushes/batch`, { method: "POST" }),
  testStart: (pid) => request(`/api/v1/projects/${pid}/test/start`, { method: "POST" }),
  testStop: (pid) => request(`/api/v1/projects/${pid}/test/stop`, { method: "POST" }),
  testClear: (pid) => request(`/api/v1/projects/${pid}/test/clear`, { method: "POST" }),
  testSpeed: (pid, fast) =>
    request(`/api/v1/projects/${pid}/test/speed`, { method: "POST", body: JSON.stringify({ fast }) }),
  raw: (pid, day = "", q = "") =>
    request(`/api/v1/projects/${pid}/raw?day=${encodeURIComponent(day)}&q=${encodeURIComponent(q)}`),
  rawDevice: (pid, id, day = "") =>
    request(`/api/v1/projects/${pid}/raw/devices/${encodeURIComponent(id)}?day=${encodeURIComponent(day)}`),
  logs: (pid, q = "") => request(`/api/v1/projects/${pid}/logs?q=${encodeURIComponent(q)}`),
  clearLogs: (pid) => request(`/api/v1/projects/${pid}/logs`, { method: "DELETE" }),
  exportPushesUrl: (pid, q, merge) =>
    `/api/v1/projects/${pid}/pushes/export?q=${encodeURIComponent(q || "")}&merge=${merge ? "true" : "false"}`,
  exportLogsUrl: (pid, q) => `/api/v1/projects/${pid}/logs/export?q=${encodeURIComponent(q || "")}`,
};

export async function downloadAuth(url, filename) {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!res.ok) throw new Error("下载失败");
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
