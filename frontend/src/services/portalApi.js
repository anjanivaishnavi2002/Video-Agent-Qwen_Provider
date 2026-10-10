// Candidate portal client (accounts, job board, applications). The token lives in sessionStorage only
// (cleared when the tab closes). Every call is authorised by the BACKEND; this file just carries the token.
import { API_BASE_URL } from "./api";

const KEY = "candidate_session";
let current = null;
try {
  current = JSON.parse(sessionStorage.getItem(KEY) || "null");
} catch {
  current = null;
}

function save(session) {
  current = session;
  try {
    if (session) sessionStorage.setItem(KEY, JSON.stringify(session));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: the sign-in just will not survive a reload */
  }
}

export const isSignedIn = () => Boolean(current?.token);
export const getAccount = () => current?.account || null;

async function call(path, { method = "GET", body, form } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/portal${path}`, {
      method,
      headers: {
        ...(current?.token ? { Authorization: `Bearer ${current.token}` } : {}),
        ...(body && !form ? { "Content-Type": "application/json" } : {}),
      },
      body: form || (body ? JSON.stringify(body) : undefined),
    });
  } catch {
    throw new Error("Cannot reach the server. Please check your connection.");
  }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== "/login") {
    save(null);
    throw new Error("Your session has expired. Please sign in again.");
  }
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(
      typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join(", ") : "Request failed"
    );
  }
  return data;
}

export async function register({ email, password, fullName, phone }) {
  const data = await call("/register", { method: "POST", body: { email, password, full_name: fullName, phone: phone || null } });
  save({ token: data.access_token, account: data.account });
  return data.account;
}

export async function signIn(email, password) {
  const data = await call("/login", { method: "POST", body: { email, password } });
  save({ token: data.access_token, account: data.account });
  return data.account;
}

export async function signOut() {
  try {
    await call("/logout", { method: "POST" });
  } catch {
    /* already invalid */
  }
  save(null);
}

export const listJobs = () => call("/jobs");
export const listApplications = () => call("/applications");
export const listMyInterviews = () => call("/interviews");
export const deleteInterview = (id) => call(`/interviews/${id}`, { method: "DELETE" });
export const attachInterview = (applicationId, interviewId) =>
  call(`/applications/${applicationId}/interview`, { method: "PUT", body: { interview_id: interviewId } });

// details = { experienceYears, phone, location, skills, file? }
export function applyToJob(jobId, consentVersion, details = {}) {
  const form = new FormData();
  form.append("consent_version", consentVersion);
  if (details.phone) form.append("phone", details.phone);
  if (details.location) form.append("location", details.location);
  if (details.experienceYears !== undefined && details.experienceYears !== "") {
    form.append("experience_years", details.experienceYears);
  }
  if (details.skills) form.append("skills", details.skills);
  if (details.file) form.append("file", details.file);
  if (details.interviewId) form.append("interview_id", details.interviewId);
  if (details.newInterview) form.append("new_interview", "true");
  return call(`/jobs/${jobId}/apply`, { method: "POST", form });
}
