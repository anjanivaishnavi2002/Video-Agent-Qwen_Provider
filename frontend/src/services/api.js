// Local dev: the backend on port 8000. Docker/production builds set VITE_API_URL=/api
// so the browser talks to the same origin and the web server proxies to the backend.
export const API_BASE_URL =
  import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

// Secret issued by the backend when the interview starts. Every later call for
// that interview must present it.
let sessionToken = null;

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

// `consentVersion` proves which consent form the candidate accepted.
export async function uploadResume(name, file, consentVersion) {
  const formData = new FormData();
  formData.append("name", name);
  formData.append("consent_version", consentVersion);
  formData.append("file", file);

  const response = await request(
    "/resume/upload",
    { method: "POST", body: formData },
    "Resume upload failed"
  );
  return response.json();
}

// ---------------------------------------------------------
// Interview
// ---------------------------------------------------------

export async function startInterview(candidateId) {
  const response = await request(
    "/session/start",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidate_id: candidateId }),
    },
    "Could not start interview"
  );
  const data = await response.json();
  sessionToken = data.session_token;
  return { sessionId: data.session_id, ...parseSpokenReply(data) };
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
export async function uploadVideo(sessionId, videoBlob) {
  const formData = new FormData();
  formData.append("video", videoBlob, videoFileName(videoBlob));

  const response = await request(
    `/session/${sessionId}/video`,
    { method: "POST", headers: authHeaders(), body: formData },
    "Video upload failed"
  );
  return response.json();
}
