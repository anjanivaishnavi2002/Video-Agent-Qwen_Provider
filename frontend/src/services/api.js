// Local dev: the backend on port 8000. Docker/production builds set VITE_API_URL=/api
// so the browser talks to the same origin and the web server proxies to the backend.
// Cloud Run: the web container writes /config.json at start (API_URL=...) and main.jsx loads it before the app, so
// ONE image works in every environment. Otherwise the build-time VITE_API_URL, then local dev.
export const API_BASE_URL = (
  (typeof window !== "undefined" && window.__APP_CONFIG__?.API_URL) ||
  import.meta.env.VITE_API_URL ||
  "http://127.0.0.1:8000"
).replace(/\/$/, "");

// Secret issued by the backend when the interview starts. Every later call for
// that interview must present it.
let sessionToken = null;

// Secret from the candidate's application / invitation link. Needed to start an interview.
let inviteToken = null;
export function setInviteToken(token) {
  inviteToken = token || null;
}

function authHeaders(extra = {}) {
  return sessionToken ? { ...extra, "X-Session-Token": sessionToken } : extra;
}

// ---------------------------------------------------------
// Helpers
// ---------------------------------------------------------

// FastAPI errors come as { detail: "text" } or, for validation
// errors (422), { detail: [{ msg: "..." }, ...] }.
async function readError(response, fallback) {
  const body = await response.json().catch(() => ({}));
  const detail = body.detail;

  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => item.msg).join(", ");
  return fallback;
}

async function request(path, options, fallbackMessage) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, options);
  } catch {
    throw new Error("Cannot reach the server. Is the backend running?");
  }

  if (!response.ok) {
    throw new Error(await readError(response, fallbackMessage));
  }
  return response;
}

function base64ToBlob(base64, type) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type });
}

// The interviewer's reply always arrives as AUDIO (the UI never shows the text).
function parseSpokenReply(data) {
  return {
    status: data.status || "ok", // "ok" | "no_speech"
    finished: Boolean(data.finished),
    audio: data.audio_base64
      ? base64ToBlob(data.audio_base64, "audio/wav")
      : null,
  };
}

function videoFileName(blob) {
  return blob.type.includes("mp4") ? "interview.mp4" : "interview.webm";
}

// ---------------------------------------------------------
// Configuration (single source of truth lives in the backend)
// ---------------------------------------------------------

export async function getPublicConfig() {
  const response = await request(
    "/config/public",
    undefined,
    "Could not load configuration"
  );
  return response.json();
}

// ---------------------------------------------------------
// Resume
// ---------------------------------------------------------

export async function getConsent() {
  const response = await request(
    "/config/consent",
    undefined,
    "Could not load the consent form"
  );
  return response.json();
}

export async function getOpenJobs() {
  const response = await request("/jobs", undefined, "Could not load open positions");
  return response.json();
}

// What the invitation link in an e-mail / SMS resolves to.
export async function getInvitation(token) {
  const response = await request(
    `/candidates/invite/${encodeURIComponent(token)}`,
    undefined,
    "This invitation link is not valid."
  );
  return response.json();
}

// `consentVersion` proves which consent form the candidate accepted.
// `details` = { email, phone, location, experienceYears, skills, jobId }
export async function uploadResume(name, file, consentVersion, details = {}) {
  const formData = new FormData();
  formData.append("name", name);
  formData.append("consent_version", consentVersion);
  formData.append("email", details.email || "");
  if (details.phone) formData.append("phone", details.phone);
  if (details.location) formData.append("location", details.location);
  if (details.experienceYears !== undefined && details.experienceYears !== "") {
    formData.append("experience_years", details.experienceYears);
  }
  if (details.skills) formData.append("skills", details.skills);
  if (details.jobId) formData.append("job_id", details.jobId);
  formData.append("file", file);

  const response = await request(
    "/resume/upload",
    { method: "POST", body: formData },
    "Resume upload failed"
  );
  const data = await response.json();
  inviteToken = data.invite_token || inviteToken;
  return data;
}

// ---------------------------------------------------------
// Interview
// ---------------------------------------------------------

export function getSessionToken() {
  return sessionToken;
}

// ws:// or wss:// URL of the Gemini Live interview socket (same host as the API).
export function liveSocketUrl(sessionId) {
  const base = API_BASE_URL.startsWith("http")
    ? API_BASE_URL
    : `${window.location.origin}${API_BASE_URL}`;
  return `${base.replace(/^http/, "ws")}/session/${sessionId}/live`;
}

export async function startInterview(candidateId) {
  const response = await request(
    "/session/start",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_id: candidateId, invite_token: inviteToken }),
    },
    "Could not start interview"
  );
  const data = await response.json();
  sessionToken = data.session_token;
  return { sessionId: data.session_id, mode: data.mode || "turn", ...parseSpokenReply(data) };
}

// `audioBlob` is a WAV of one complete answer (recorded hands-free).
export async function sendVoiceAnswer(sessionId, audioBlob) {
  const formData = new FormData();
  // Do NOT set Content-Type yourself; the browser adds the boundary.
  formData.append("audio", audioBlob, "answer.wav");

  const response = await request(
    `/session/${sessionId}/voice-answer`,
    { method: "POST", headers: authHeaders(), body: formData },
    "Voice answer failed"
  );
  return parseSpokenReply(await response.json());
}

// The candidate stayed silent for the configured time: the interviewer checks in.
export async function sendNoResponse(sessionId) {
  const response = await request(
    `/session/${sessionId}/no-response`,
    { method: "POST", headers: authHeaders() },
    "Could not continue the interview"
  );
  return parseSpokenReply(await response.json());
}

export async function endInterview(sessionId, reason = "candidate_ended") {
  const response = await request(
    `/session/${sessionId}/end`,
    {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ reason }),
    },
    "Could not end interview"
  );
  return response.json();
}

// ---------------------------------------------------------
// Monitoring events + recording
// ---------------------------------------------------------

export async function sendEvents(sessionId, events) {
  const response = await request(
    `/session/${sessionId}/events`,
    {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ events }),
    },
    "Could not save monitoring events"
  );
  return response.json();
}

// Upload the recording of the whole interview (camera + microphone).
// Production (Cloud Run limits request bodies to 32 MiB): the backend hands out a short-lived signed URL and the
// browser uploads straight to the private bucket. Locally the plain multipart endpoint is used.
export async function uploadVideo(sessionId, videoBlob) {
  const contentType = (videoBlob.type || "video/webm").split(";")[0];
  const plan = await (
    await request(
      `/session/${sessionId}/video/upload-url`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ content_type: contentType }),
      },
      "Could not prepare the recording upload"
    )
  ).json();

  if (plan.mode === "gcs") {
    let put;
    try {
      put = await fetch(plan.url, {
        method: "PUT",
        headers: { "Content-Type": plan.content_type, ...plan.headers },
        body: videoBlob,
      });
    } catch {
      throw new Error("Video upload failed (network).");
    }
    if (!put.ok) throw new Error("Video upload failed");
    const done = await request(
      `/session/${sessionId}/video/complete`,
      {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ content_type: plan.content_type }),
      },
      "Video upload failed"
    );
    return done.json();
  }

  const formData = new FormData();
  formData.append("video", videoBlob, videoFileName(videoBlob));
  const response = await request(
    `/session/${sessionId}/video`,
    { method: "POST", headers: authHeaders(), body: formData },
    "Video upload failed"
  );
  return response.json();
}

// ---------------------------------------------------------
// Post-interview report (summary + scorecard + recording events)
// ---------------------------------------------------------

export async function getInterviewReport(sessionId) {
  const response = await request(
    `/session/${sessionId}/result`,
    { headers: authHeaders() },
    "Could not load the interview report"
  );
  const data = await response.json();
  return data.summary || null;
}

export async function generateInterviewReport(sessionId) {
  const response = await request(
    `/session/${sessionId}/summary`,
    { method: "POST", headers: authHeaders() },
    "Could not create the interview report"
  );
  return response.json();
}

// Wait (briefly) for the backend to finish the report; ask for it directly if it did not start by itself.
export async function waitForInterviewReport(sessionId, { tries = 8, delayMs = 3000 } = {}) {
  for (let attempt = 0; attempt < tries; attempt += 1) {
    const current = await getInterviewReport(sessionId);
    if (current && current.status) return current;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  return generateInterviewReport(sessionId);
}
