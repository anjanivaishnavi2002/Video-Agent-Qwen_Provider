// Admin API client. The JWT lives in sessionStorage (cleared when the tab closes) - never in localStorage, and
// never in the URL. Every call is authorised by the BACKEND; hiding screens here is only a convenience.
import { API_BASE_URL } from "../services/api";

const KEY = "admin_session";

export function loadSession() {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || "null");
  } catch {
    return null;
  }
}

function saveSession(session) {
  try {
    if (session) sessionStorage.setItem(KEY, JSON.stringify(session));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: the session just won't survive a reload */
  }
}

let current = loadSession();
let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}
export function getAdmin() {
  return current?.admin || null;
}
export function isSignedIn() {
  return Boolean(current?.token);
}

async function call(path, { method = "GET", body, params, raw, form } = {}) {
  const url = new URL(`${API_BASE_URL}/admin${path}`, window.location.origin);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  });
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: {
        ...(current?.token ? { Authorization: `Bearer ${current.token}` } : {}),
        ...(body && !form ? { "Content-Type": "application/json" } : {}),
      },
      body: form || (body ? JSON.stringify(body) : undefined),
    });
  } catch {
    throw new Error("Cannot reach the server.");
  }
  if (response.status === 401 && path !== "/auth/login") {
    current = null;
    saveSession(null);
    onUnauthorized();
    throw new Error("Your session has expired. Please sign in again.");
  }
  if (raw) return response;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg).join(", ")
          : "Request failed"
    );
  }
  return data;
}

export async function login(email, password) {
  const data = await call("/auth/login", { method: "POST", body: { email, password } });
  current = { token: data.access_token, admin: data.admin };
  saveSession(current);
  return data.admin;
}

export async function logout() {
  try {
    await call("/auth/logout", { method: "POST" });
  } catch {
    /* already invalid */
  }
  current = null;
  saveSession(null);
}

// Resume / recording: the backend answers with a short-lived signed URL (production) or the file itself (local).
export async function openFile(path) {
  const response = await call(path, { raw: true });
  if (!response.ok) throw new Error("File not available");
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    const data = await response.json();
    window.open(data.url, "_blank", "noopener,noreferrer");
  } else {
    const blob = await response.blob();
    window.open(URL.createObjectURL(blob), "_blank", "noopener,noreferrer");
  }
}

export const api = {
  stats: () => call("/dashboard/stats"),
  candidates: (params) => call("/candidates", { params }),
  candidate: (id) => call(`/candidates/${id}`),
  updateCandidate: (id, body) => call(`/candidates/${id}`, { method: "PATCH", body }),
  deleteCandidate: (id) => call(`/candidates/${id}`, { method: "DELETE" }),
  invite: (id, channels) => call(`/candidates/${id}/invite`, { method: "POST", body: { channels } }),
  notify: (id, kind, channels, message, subject) =>
    call(`/candidates/${id}/notify`, { method: "POST", body: { kind, channels, message, subject } }),
  jobs: (params) => call("/jobs", { params }),
  addSampleJobs: () => call("/jobs/sample", { method: "POST" }),
  createJob: (body) => call("/jobs", { method: "POST", body }),
  updateJob: (id, body) => call(`/jobs/${id}`, { method: "PATCH", body }),
  deleteJob: (id) => call(`/jobs/${id}`, { method: "DELETE" }),
  extractJd: (file) => {
    const form = new FormData();
    form.append("file", file);
    return call("/jobs/extract-jd", { method: "POST", form });
  },
  uploadJd: (id, file, fill = false) => {
    const form = new FormData();
    form.append("file", file);
    return call(`/jobs/${id}/jd`, { method: "POST", params: { fill_description: fill ? "true" : "" }, form });
  },
  credits: () => call("/credits"),
  grantCredits: (amount, note) => call("/credits/grant", { method: "POST", body: { amount, note } }),
  unlock: (id) => call(`/interviews/${id}/unlock`, { method: "POST" }),
  interviews: (params) => call("/interviews", { params }),
  interview: (id) => call(`/interviews/${id}`),
  evaluate: (id) => call(`/interviews/${id}/evaluate`, { method: "POST" }),
  regenerateReport: (id) => call(`/interviews/${id}/report`, { method: "POST" }),
  notifications: (params) => call("/notifications", { params }),
  admins: () => call("/users"),
  createAdmin: (body) => call("/users", { method: "POST", body }),
  updateAdmin: (id, body) => call(`/users/${id}`, { method: "PATCH", body }),
};
