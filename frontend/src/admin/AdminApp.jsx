import { useCallback, useEffect, useRef, useState } from "react";
import "./admin.css";
import {
  api, getAdmin, isSignedIn, login, logout, openFile, setUnauthorizedHandler,
} from "./adminApi";

const STATUSES = ["applied", "invited", "in_progress", "completed", "shortlisted", "rejected", "on_hold"];
const fmt = (v) => (v ? new Date(v).toLocaleString() : "-");
const label = (s) => (s || "-").replaceAll("_", " ");

function Badge({ value }) {
  return <span className={`badge b-${value}`}>{label(value)}</span>;
}

// `key` is a string that changes whenever the request should be repeated (e.g. JSON of the filters).
function useLoad(fn, key) {
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });
  const [state, setState] = useState({ data: null, error: "", loading: true });
  const reload = useCallback(() => {
    fnRef.current().then(
      (data) => setState({ data, error: "", loading: false }),
      (err) => setState({ data: null, error: err.message, loading: false })
    );
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    reload();
  }, [reload]);
  return { ...state, reload };
}

function Pager({ page, total, size, onPage }) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="pager">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span>Page {page} of {pages} · {total} total</span>
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  );
}

// ---------------------------------------------------------------- login
function Login({ onSignedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email.trim(), password);
      window.history.replaceState({}, "", "/admin");
      onSignedIn();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-login">
      <form onSubmit={submit} className="card">
        <h1>Admin sign in</h1>
        <p className="muted">Recruiters and administrators only.</p>
        <label htmlFor="admin-email">E-mail</label>
        <input id="admin-email" type="email" autoComplete="username" value={email}
          onChange={(e) => setEmail(e.target.value)} required />
        <label htmlFor="admin-password">Password</label>
        <input id="admin-password" type="password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} required />
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------- dashboard
function Dashboard() {
  const { data, error } = useLoad(api.stats, JSON.stringify([]));
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading...</p>;
  const tiles = [
    ["Candidates", data.candidates_total],
    ["Open jobs", data.open_jobs],
    ["Interviews running", data.interviews_running],
    ["Interviews (7 days)", data.interviews_last_7_days],
    ["Average AI score", data.average_score == null ? "-" : Math.round(data.average_score)],
    ["Failed notifications (7 days)", data.notifications_failed_last_7_days],
  ];
  return (
    <>
      <div className="tiles">
        {tiles.map(([k, v]) => (
          <div className="tile" key={k}><strong>{v}</strong><span>{k}</span></div>
        ))}
      </div>
      <h3>Candidates by status</h3>
      <div className="chips">
        {Object.entries(data.candidates_by_status).map(([k, v]) => (
          <span key={k} className="chip"><Badge value={k} /> {v}</span>
        ))}
      </div>
    </>
  );
}

// Compose and send a message to the candidate by e-mail, SMS and/or an automated phone call.
function ContactPanel({ candidate, onSent }) {
  const [channels, setChannels] = useState({ email: !!candidate.email, sms: !!candidate.phone, call: false });
  const [kind, setKind] = useState("custom");
  const [subject, setSubject] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  const picked = Object.keys(channels).filter((k) => channels[k]);
  const missing = (k) => (k === "email" ? !candidate.email : !candidate.phone);

  async function send() {
    setResult("");
    if (!picked.length) return setResult("Choose at least one channel.");
    if (kind === "custom" && !text.trim()) return setResult("Write a message first.");
    if (picked.includes("call") && !window.confirm(`Place an automated phone call to ${candidate.phone}?`)) return;
    setBusy(true);
    try {
      const res = await api.notify(candidate.id, kind, picked, kind === "custom" ? text : undefined,
        kind === "custom" ? subject : undefined);
      const rows = res.notifications || [];
      setResult(rows.map((n) => `${n.channel}: ${n.status}${n.error ? ` (${n.error})` : ""}`).join(" | ") || "Nothing sent");
      onSent();
    } catch (err) {
      setResult(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <fieldset className="contact">
      <legend>Contact candidate</legend>
      <div className="row wrap">
        {["email", "sms", "call"].map((k) => (
          <label key={k} className="check">
            <input type="checkbox" checked={channels[k]} disabled={missing(k)}
              onChange={(e) => setChannels({ ...channels, [k]: e.target.checked })} />
            {k === "email" ? "E-mail" : k === "sms" ? "SMS" : "Phone call"}
            {missing(k) && <span className="muted small"> (no {k === "email" ? "e-mail" : "phone"})</span>}
          </label>
        ))}
      </div>
      <label>Message
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="custom">Write my own message</option>
          <option value="invitation">Interview invitation</option>
          <option value="reminder">Interview reminder</option>
          <option value="status_update">Application status update</option>
        </select>
      </label>
      {kind === "custom" && (
        <>
          {channels.email && <input placeholder="E-mail subject (optional)" value={subject} maxLength={200}
            onChange={(e) => setSubject(e.target.value)} />}
          <textarea rows={4} maxLength={1000} placeholder="Type your message. SMS is trimmed to 480 characters; a call reads it aloud."
            value={text} onChange={(e) => setText(e.target.value)} />
        </>
      )}
      <button disabled={busy} onClick={send}>{busy ? "Sending..." : "Send"}</button>
      {result && <p className="notice" role="status">{result}</p>}
    </fieldset>
  );
}

// ---------------------------------------------------------------- candidates
function CandidateDetail({ id, onClose, onChanged, jobs }) {
  const { data: c, error, reload } = useLoad(() => api.candidate(id), JSON.stringify([id]));
  const [msg, setMsg] = useState("");
  const [open, setOpen] = useState(null);

  async function run(fn, ok) {
    setMsg("");
    try {
      await fn();
      setMsg(ok);
      reload();
      onChanged();
    } catch (err) {
      setMsg(err.message);
    }
  }

  if (error) return <aside className="drawer"><button onClick={onClose}>Close</button><p className="error">{error}</p></aside>;
  if (!c) return <aside className="drawer"><p className="muted">Loading...</p></aside>;
  const ev = c.latest_evaluation;
  return (
    <aside className="drawer">
      <div className="row between"><h2>{c.name}</h2><button onClick={onClose}>Close</button></div>
      <p><Badge value={c.interview_status} /> {c.job_title ? `· ${c.job_title}` : ""}</p>
      <dl>
        <dt>E-mail</dt><dd>{c.email || "-"}</dd>
        <dt>Phone</dt><dd>{c.phone || "-"}</dd>
        <dt>Location</dt><dd>{c.location || "-"}</dd>
        <dt>Experience</dt><dd>{c.experience_years ?? "-"} years</dd>
        <dt>Skills</dt><dd>{(c.skills || []).join(", ") || "-"}</dd>
        <dt>Attempts</dt><dd>{c.interview_attempts}</dd>
        <dt>AI score</dt><dd>{c.interview_score ?? "-"}</dd>
        <dt>Applied</dt><dd>{fmt(c.created_at)}</dd>
      </dl>

      <div className="row wrap">
        {c.has_resume && <button onClick={() => openFile(`/candidates/${c.id}/resume`).catch((e) => setMsg(e.message))}>Open resume</button>}
        <button onClick={() => run(() => api.invite(c.id), "Invitation requested")}>Send invitation</button>
        <button onClick={() => run(() => api.notify(c.id, "reminder"), "Reminder requested")}>Send reminder</button>
        <button onClick={() => run(() => api.notify(c.id, "status_update"), "Status e-mail/SMS requested")}>Notify status</button>
        <button onClick={() => run(() => api.updateCandidate(c.id, { reset_attempts: true }), "Attempts reset")}>Reset attempts</button>
      </div>
      <div className="row">
        <label>Status
          <select value={c.interview_status}
            onChange={(e) => run(() => api.updateCandidate(c.id, { interview_status: e.target.value }), "Status updated")}>
            {STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
          </select>
        </label>
        <label>Job
          <select value={c.job_id || ""}
            onChange={(e) => run(() => api.updateCandidate(c.id, { job_id: e.target.value ? Number(e.target.value) : null }), "Job updated")}>
            <option value="">-</option>
            {jobs.map((j) => <option key={j.id} value={j.id}>{j.title}</option>)}
          </select>
        </label>
      </div>
      {msg && <p className="notice" role="status">{msg}</p>}
      <ContactPanel candidate={c} onSent={reload} />

      {ev && (
        <section>
          <h3>AI evaluation <Badge value={ev.recommendation} /> {ev.overall_score != null && <b>{Math.round(ev.overall_score)}/100</b>}</h3>
          <p>{ev.summary}</p>
          {ev.strengths?.length > 0 && <><h4>Strengths</h4><ul>{ev.strengths.map((s, i) => <li key={i}>{s}</li>)}</ul></>}
          {ev.concerns?.length > 0 && <><h4>Concerns</h4><ul>{ev.concerns.map((s, i) => <li key={i}>{s}</li>)}</ul></>}
          <p className="muted">Decision support only. A person makes the hiring decision.</p>
        </section>
      )}

      <section>
        <h3>Interview history</h3>
        {c.interviews.length === 0 && <p className="muted">No interviews yet.</p>}
        {c.interviews.map((i) => (
          <div key={i.id} className="row between item">
            <span>#{i.attempt_number} · <Badge value={i.status} /> · {fmt(i.started_at)} · score {i.overall_score == null ? "-" : Math.round(i.overall_score)}</span>
            <button onClick={() => setOpen(i.id)}>Details</button>
          </div>
        ))}
      </section>

      <section>
        <h3>Notifications</h3>
        {c.notifications.length === 0 && <p className="muted">None sent.</p>}
        {c.notifications.map((n) => (
          <div key={n.id} className="item small">
            {fmt(n.created_at)} · {n.channel} · {label(n.kind)} · <Badge value={n.status} />
            {n.error ? <span className="muted"> — {n.error}</span> : null}
          </div>
        ))}
      </section>
      {open && <InterviewDetail id={open} onClose={() => setOpen(null)} />}
    </aside>
  );
}

function Candidates({ jobs }) {
  const [filters, setFilters] = useState({ q: "", status: "", job_id: "", sort: "newest" });
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);
  const { data, error, reload } = useLoad(() => api.candidates({ ...filters, page, page_size: 25 }), JSON.stringify([filters, page]));
  const set = (k) => (e) => { setPage(1); setFilters({ ...filters, [k]: e.target.value }); };

  return (
    <>
      <div className="filters">
        <input placeholder="Search name, e-mail, phone, location" value={filters.q} onChange={set("q")} />
        <select value={filters.status} onChange={set("status")}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
        </select>
        <select value={filters.job_id} onChange={set("job_id")}>
          <option value="">All jobs</option>
          {jobs.map((j) => <option key={j.id} value={j.id}>{j.title}</option>)}
        </select>
        <select value={filters.sort} onChange={set("sort")}>
          <option value="newest">Newest</option><option value="oldest">Oldest</option>
          <option value="score">Highest score</option><option value="name">Name</option>
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      <table>
        <thead><tr><th>Name</th><th>Job</th><th>Status</th><th>Score</th><th>Attempts</th><th>Applied</th></tr></thead>
        <tbody>
          {(data?.items || []).map((c) => (
            <tr key={c.id} onClick={() => setSelected(c.id)} className="click">
              <td>{c.name}<div className="muted small">{c.email}</div></td>
              <td>{c.job_title || "-"}</td>
              <td><Badge value={c.interview_status} /></td>
              <td>{c.interview_score == null ? "-" : Math.round(c.interview_score)}</td>
              <td>{c.interview_attempts}</td>
              <td>{fmt(c.created_at)}</td>
            </tr>
          ))}
          {data && data.items.length === 0 && <tr><td colSpan="6" className="muted">No candidates match.</td></tr>}
        </tbody>
      </table>
      {data && <Pager page={page} total={data.total} size={25} onPage={setPage} />}
      {selected && <CandidateDetail id={selected} jobs={jobs} onClose={() => setSelected(null)} onChanged={reload} />}
    </>
  );
}

// ---------------------------------------------------------------- interviews
const EVENT_LABELS = {
  face_missing: "Face not in view",
  face_returned: "Face back in view",
  multiple_faces: "More than one face in view",
  head_movement: "Head movement",
  tab_hidden: "Left the page (tab / window switch)",
};

// Reviewer report: written-work scores, camera counts (faces detected) and tab switches. The candidate only sees feedback.
function ReviewerReport({ i }) {
  const summary = i.summary || {};
  const events = summary.recording?.events;
  const counts = events?.counts || {};
  const chat = summary.chat;
  const work = i.chat_work || [];
  const switches = i.tab_switch_count || 0;
  return (
    <>
      <section>
        <h3>Proctoring</h3>
        <ul className="small">
          <li>Format: <b>{i.mode === "chat" ? "Chat assessment" : "Voice interview"}</b></li>
          <li>
            Tab / window switches: <b>{switches}</b>
            {i.end_reason === "tab_switch_limit" && " - the interview was ended automatically after the 2 warnings"}
          </li>
          <li>Most faces seen at once: <b>{events?.max_faces_in_view ?? "no camera data"}</b></li>
          <li>Times more than one face was in view: <b>{events?.multiple_face_events ?? 0}</b></li>
          <li>Times the face left the view: <b>{counts.face_missing ?? 0}</b> ({events?.face_missing_seconds ?? 0} s in total)</li>
          <li>Head movement events: <b>{counts.head_movement ?? 0}</b></li>
          <li>Recording saved: <b>{i.has_recording ? "yes" : "no"}</b></li>
        </ul>
        {events?.note && <p className="muted small">{events.note}</p>}
      </section>

      {summary.overview || summary.interview?.overview ? (
        <section><h3>Summary</h3><p>{summary.interview?.overview || summary.overview}</p></section>
      ) : null}
      {summary.status === "failed" && <p className="error">The report failed: {summary.error}</p>}
      {summary.status === "skipped" && <p className="muted">{summary.reason}</p>}

      {chat && (
        <section>
          <h3>Written assessment {chat.overall_score != null && <b>{chat.overall_score} / 5</b>}</h3>
          {chat.tasks.map((t) => {
            const item = work.find((x) => x.task_id === t.task_id) || {};
            const w = item.work || {};
            return (
              <div key={t.task_id} className="task-review">
                <h4>{t.title} <span className="muted small">({t.kind})</span></h4>
                {t.kind === "email" ? (
                  (item.emails?.length ? item.emails : [{ id: "e1", from_name: "Customer", subject: t.title, body: "" }]).map((m) => {
                    const r = (w.replies || {})[m.id] || (m.id === "e1" ? w : {});
                    return (
                      <div key={m.id}>
                        <p className="muted small"><b>{m.from_name}</b> - {m.subject}: {m.body}</p>
                        <blockquote><b>{r.subject || "(no subject)"}</b><br />{(r.body || "(not answered)").split("\n").map((line, k) => <span key={k}>{line}<br /></span>)}</blockquote>
                      </div>
                    );
                  })
                ) : (
                  <div className="transcript">
                    {(w.messages || []).map((m, k) => <p key={k} className={m.role}><b>{m.role === "agent" ? "Candidate" : "Customer"}:</b> {m.text}</p>)}
                  </div>
                )}
                <table><tbody>
                  {t.criteria.map((c, k) => <tr key={k}><td>{c.name}</td><td><b>{c.score}/5</b></td><td className="muted small">{c.evidence}</td></tr>)}
                </tbody></table>
                <p className="small">{t.feedback}</p>
              </div>
            );
          })}
          {chat.strengths?.length > 0 && <p className="small"><b>Strengths:</b> {chat.strengths.join("; ")}</p>}
          {chat.areas_to_probe?.length > 0 && <p className="small"><b>Worth probing:</b> {chat.areas_to_probe.join("; ")}</p>}
          <p className="muted small">{chat.note}</p>
        </section>
      )}

      {summary.scorecard?.status === "ready" && (
        <section>
          <h3>Scorecard <b>{summary.scorecard.overall_score} / 5</b></h3>
          <table><tbody>
            {summary.scorecard.criteria.map((c, k) => <tr key={k}><td>{c.name}</td><td><b>{c.score}/5</b></td><td className="muted small">{c.evidence}</td></tr>)}
          </tbody></table>
        </section>
      )}

      {i.events.length > 0 && (
        <section><h3>Event timeline</h3>
          <ul>{i.events.map((e, k) => (
            <li key={k} className="small">{EVENT_LABELS[e.type] || e.type} at {Math.round((e.offset_ms || 0) / 1000)}s{e.type === "multiple_faces" && e.details?.faces ? ` (${e.details.faces} faces)` : ""}</li>
          ))}</ul>
        </section>
      )}
    </>
  );
}

function Teaser({ i, onClose, onUnlocked }) {
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const mins = i.duration_seconds != null ? Math.max(1, Math.round(i.duration_seconds / 60)) : null;
  async function unlock() {
    if (!window.confirm(`Unlock this interview for ${i.unlock_cost} credits? You have ${i.credit_balance}.`)) return;
    setBusy(true);
    try { await api.unlock(i.id); onUnlocked(); } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal" role="dialog" aria-label="Interview preview">
      <div className="card wide teaser">
        <div className="row between"><h2>Interview preview</h2><button onClick={onClose}>Close</button></div>
        <p><b>{(i.jobs?.length ? i.jobs.map((j) => j.title).join(", ") : i.job?.title) || "No job"}</b> · {i.mode === "chat" ? "Written assessment" : "Voice interview"} · <Badge value={i.status} />{mins ? ` · ${mins} min` : ""}</p>
        <div className="teaser-grid">
          <div><span className="muted small">Candidate</span><b>{i.candidate?.name || "-"}</b></div>
          <div><span className="muted small">Experience</span><b>{i.candidate?.experience_years ?? "-"} yrs</b></div>
          <div><span className="muted small">AI score</span><b>{i.overall_score == null ? "-" : `${Math.round(i.overall_score)}/100`}</b></div>
          <div><span className="muted small">Recommendation</span><b>{i.recommendation ? label(i.recommendation) : "-"}</b></div>
          <div><span className="muted small">Tab switches</span><b>{i.tab_switch_count}</b></div>
          <div><span className="muted small">Recording</span><b>{i.has_recording ? "Available" : "None"}</b></div>
        </div>
        {i.teaser_summary && <p>{i.teaser_summary}</p>}
        {i.strengths?.length > 0 && <ul>{i.strengths.map((t, k) => <li key={k} className="small">{t}</li>)}</ul>}
        <div className="unlock-box">
          <div><b>Unlock the full interview</b><span className="muted small"> recording, transcript, written work, face and tab report, contact details</span></div>
          <button className="primary" disabled={busy} onClick={unlock}>Unlock for {i.unlock_cost} credits</button>
        </div>
        <p className="muted small">Your balance: {i.credit_balance} credits. Unlocking the same interview again never charges twice.</p>
        {msg && <p className="error">{msg}</p>}
      </div>
    </div>
  );
}

function InterviewDetail({ id, onClose }) {
  const { data: i, error, reload } = useLoad(() => api.interview(id), JSON.stringify([id]));
  const [msg, setMsg] = useState("");
  if (error) return <div className="modal"><div className="card"><button onClick={onClose}>Close</button><p className="error">{error}</p></div></div>;
  if (!i) return null;
  if (i.locked) return <Teaser i={i} onClose={onClose} onUnlocked={reload} />;
  const ev = i.evaluation;
  return (
    <div className="modal" role="dialog" aria-label="Interview details">
      <div className="card wide">
        <div className="row between">
          <h2>Interview #{i.id} · {i.candidate?.name}</h2><button onClick={onClose}>Close</button>
        </div>
        <p><Badge value={i.status} /> {i.end_reason ? `· ended: ${label(i.end_reason)}` : ""} · {fmt(i.started_at)}</p>
        <div className="row wrap">
          {i.has_recording && <button onClick={() => openFile(`/interviews/${i.id}/recording`).catch((e) => setMsg(e.message))}>Open recording</button>}
          {i.mode !== "chat" && <button onClick={() => api.evaluate(i.id).then(() => { setMsg("Evaluation updated"); reload(); }, (e) => setMsg(e.message))}>Re-run AI evaluation</button>}
          <button onClick={() => api.regenerateReport(i.id).then(() => { setMsg("Report rebuilt"); reload(); }, (e) => setMsg(e.message))}>Rebuild report</button>
        </div>
        {msg && <p className="notice">{msg}</p>}
        {ev && (
          <section>
            <h3>AI evaluation <Badge value={ev.recommendation} /> {ev.overall_score != null && <b>{Math.round(ev.overall_score)}/100</b>}</h3>
            <p>{ev.summary}</p>
            {ev.criteria?.length > 0 && (
              <table><tbody>{ev.criteria.map((c, k) => <tr key={k}><td>{c.name}</td><td>{Math.round(c.score)}</td><td className="muted small">{c.evidence}</td></tr>)}</tbody></table>
            )}
          </section>
        )}
        <ReviewerReport i={i} />
        {i.mode !== "chat" && <section>
          <h3>Transcript</h3>
          <div className="transcript">
            {i.transcript.map((t, k) => (
              <p key={k} className={t.role}><b>{t.role === "assistant" ? "Interviewer" : "Candidate"}:</b> {t.text}</p>
            ))}
          </div>
        </section>}
      </div>
    </div>
  );
}

function Interviews({ jobs, initialJobId = "" }) {
  const [status, setStatus] = useState("");
  const [jobId, setJobId] = useState(initialJobId ? String(initialJobId) : "");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(null);
  const { data, error, reload } = useLoad(() => api.interviews({ status, job_id: jobId, page, page_size: 25 }), JSON.stringify([status, jobId, page]));
  return (
    <>
      <div className="filters">
        <select value={jobId} onChange={(e) => { setPage(1); setJobId(e.target.value); }}>
          <option value="">All jobs</option>
          {jobs.map((j) => <option key={j.id} value={j.id}>{j.title}</option>)}
        </select>
        <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
          <option value="">All statuses</option>
          {["running", "finished", "ended_early", "failed"].map((s) => <option key={s} value={s}>{label(s)}</option>)}
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      <table>
        <thead><tr><th>#</th><th>Candidate</th><th>Job</th><th>Type</th><th>Status</th><th>AI score</th><th>Recommendation</th><th>Recording</th><th>Started</th></tr></thead>
        <tbody>
          {(data?.items || []).map((i) => (
            <tr key={i.id} className="click" onClick={() => setOpen(i.id)}>
              <td>{i.id}</td><td>{i.candidate_name}</td><td>{i.job_title || "-"}</td><td>{i.mode === "chat" ? "Written" : "Voice"}</td>
              <td><Badge value={i.status} /></td>
              <td>{i.overall_score == null ? "-" : Math.round(i.overall_score)}</td>
              <td>{i.recommendation ? <Badge value={i.recommendation} /> : "-"}</td>
              <td>{!i.has_recording ? "-" : i.unlocked ? "Unlocked" : "Locked"}</td>
              <td>{fmt(i.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && <Pager page={page} total={data.total} size={25} onPage={setPage} />}
      {open && <InterviewDetail id={open} onClose={() => { setOpen(null); reload(); }} />}
    </>
  );
}

// ---------------------------------------------------------------- jobs
const expBand = (j) => (j.experience_min == null && j.experience_max == null ? "Any"
  : j.experience_max == null ? `${j.experience_min}+ yrs` : `${j.experience_min ?? 0}-${j.experience_max} yrs`);

function JobForm({ job, onDone, onCancel }) {
  const [f, setF] = useState({
    title: job?.title || "", description: job?.description || "", department: job?.department || "",
    location: job?.location || "", employment_type: job?.employment_type || "",
    required_skills: (job?.required_skills || []).join(", "), status: job?.status || "open",
    process_type: job?.process_type || "", experience_min: job?.experience_min ?? "", experience_max: job?.experience_max ?? "",
  });
  const [error, setError] = useState("");
  const [jdFile, setJdFile] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  async function pickJd(e) {
    const file = e.target.files?.[0];
    setError("");
    if (!file) return;
    try {
      const { text } = await api.extractJd(file);
      setJdFile(file);
      setF((cur) => ({ ...cur, description: text }));
    } catch (err) { setError(err.message); setJdFile(null); }
  }
  async function save(e) {
    e.preventDefault();
    setError("");
    const num = (v) => (v === "" || v == null ? null : Number(v));
    const body = { ...f, required_skills: f.required_skills.split(",").map((s) => s.trim()).filter(Boolean),
      process_type: f.process_type || null, experience_min: num(f.experience_min), experience_max: num(f.experience_max) };
    try {
      const saved = job ? await api.updateJob(job.id, body) : await api.createJob(body);
      if (jdFile) await api.uploadJd(saved.id, jdFile, false);
      onDone();
    } catch (err) { setError(err.message); }
  }
  return (
    <form className="card" onSubmit={save}>
      <h3>{job ? "Edit job" : "New job"}</h3>
      <label>Title<input value={f.title} onChange={set("title")} required /></label>
      <label>Upload a JD document (PDF, Word or text) - the text fills the box below
        <input type="file" accept=".pdf,.docx,.txt" onChange={pickJd} /></label>
      {jdFile && <p className="muted small">Attached: {jdFile.name}</p>}
      {!jdFile && job?.has_jd_file && (
        <p className="muted small">Current document: {job.jd_file_name}{" "}
          <button type="button" onClick={() => openFile(`/jobs/${job.id}/jd`).catch((e) => setError(e.message))}>Open</button></p>
      )}
      <label>Job description (candidates see this; the AI interviewer reads it)
        <textarea rows="7" value={f.description} onChange={set("description")} required minLength="10" /></label>
      <div className="row wrap">
        <label>Department<input value={f.department} onChange={set("department")} /></label>
        <label>Location<input value={f.location} onChange={set("location")} /></label>
        <label>Type<input value={f.employment_type} onChange={set("employment_type")} placeholder="full_time" /></label>
      </div>
      <div className="row wrap">
        <label>Process
          <select value={f.process_type} onChange={set("process_type")}>
            <option value="">Not set</option>
            {[["voice", "Voice"], ["chat", "Chat support"], ["email", "Email support"], ["blended", "Blended (chat + email + voice)"], ["back_office", "Back office"], ["other", "Other"]]
              .map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select></label>
        <label>Experience from (years)<input type="number" min="0" max="50" value={f.experience_min} onChange={set("experience_min")} /></label>
        <label>Experience up to (years)<input type="number" min="0" max="50" value={f.experience_max} onChange={set("experience_max")} /></label>
      </div>
      <label>Required skills (comma separated)<input value={f.required_skills} onChange={set("required_skills")} /></label>
      <label>Status
        <select value={f.status} onChange={set("status")}>{["draft", "open", "closed"].map((s) => <option key={s}>{s}</option>)}</select>
      </label>
      {error && <p className="error">{error}</p>}
      <div className="row"><button className="primary">Save</button><button type="button" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}

function Jobs({ onChanged, onViewInterviews }) {
  const { data, error, reload } = useLoad(() => api.jobs({ page_size: 100 }), JSON.stringify([]));
  const [editing, setEditing] = useState(null);   // null | "new" | job
  const done = () => { setEditing(null); reload(); onChanged(); };
  return (
    <>
      <div className="row between"><h3>Jobs</h3>
        <span className="row">
          <button onClick={() => api.addSampleJobs().then((r) => { window.alert(r.added ? `Added ${r.added} sample job(s).` : "The sample jobs are already there."); done(); }, (e) => window.alert(e.message))}>Add sample BPO jobs</button>
          <button className="primary" onClick={() => setEditing("new")}>New job</button>
        </span></div>
      {data && data.items.length === 0 && <p className="hint">No jobs yet. Click "Add sample BPO jobs" or "New job".</p>}
      {error && <p className="error">{error}</p>}
      {editing && <JobForm job={editing === "new" ? null : editing} onDone={done} onCancel={() => setEditing(null)} />}
      <table>
        <thead><tr><th>Title</th><th>Process</th><th>Experience</th><th>Location</th><th>Status</th><th>Candidates</th><th /></tr></thead>
        <tbody>
          {(data?.items || []).map((j) => (
            <tr key={j.id}>
              <td>{j.title}</td><td>{j.process_type ? label(j.process_type) : "-"}</td>
              <td>{expBand(j)}</td><td>{j.location || "-"}</td><td><Badge value={j.status} /></td><td>{j.candidate_count}</td>
              <td className="row">
                <button onClick={() => onViewInterviews?.(j.id)}>Interviews</button>
                <button onClick={() => setEditing(j)}>Edit</button>
                {getAdmin()?.role === "admin" && (
                  <button onClick={() => window.confirm("Delete this job? Jobs with applications are closed instead.") &&
                    api.deleteJob(j.id).then(done, (e) => window.alert(e.message))}>Delete</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

// ---------------------------------------------------------------- credits
function Credits() {
  const { data, error, reload } = useLoad(() => api.credits(), JSON.stringify([]));
  const [upgrade, setUpgrade] = useState(null);       // a plan, or {} for a custom amount
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  const isAdmin = getAdmin()?.role === "admin";
  async function grant(e) {
    e.preventDefault();
    setMsg("");
    try {
      await api.grantCredits(Number(amount), note || (upgrade?.name ? `${upgrade.name} pack` : ""));
      setAmount(""); setNote(""); setUpgrade(null); reload();
    } catch (err) { setMsg(err.message); }
  }
  const pct = data && data.granted_total ? Math.min(100, data.used_percent) : 0;
  return (
    <>
      <div className="row between"><h3>Credits & usage</h3>
        <button className="primary" onClick={() => { setUpgrade({}); setAmount(""); }}>Upgrade</button></div>
      {error && <p className="error">{error}</p>}
      {data?.low_balance && (
        <p className="alert-warn" role="status">Running low: {data.balance} credits left (about {data.unlocks_left} unlock{data.unlocks_left === 1 ? "" : "s"}).
          {data.locked_interviews_waiting > 0 && ` ${data.locked_interviews_waiting} interview(s) are waiting to be unlocked.`}</p>
      )}
      {data && (
        <div className="stat-cards">
          <div className="stat"><span className="muted small">Remaining</span><b>{data.balance}</b><span className="muted small">≈ {data.unlocks_left} unlocks</span></div>
          <div className="stat"><span className="muted small">Used</span><b>{data.used_total}</b><span className="muted small">{data.unlocks} interviews unlocked</span></div>
          <div className="stat"><span className="muted small">Total added</span><b>{data.granted_total}</b><span className="muted small">{data.unlock_cost} credits per unlock</span></div>
          <div className="stat"><span className="muted small">Waiting</span><b>{data.locked_interviews_waiting}</b><span className="muted small">locked interviews</span></div>
        </div>
      )}
      {data && (
        <div className="usage-bar" aria-label={`${pct}% of credits used`}>
          <div style={{ width: `${pct}%` }} /><span className="muted small">{pct}% of credits used</span>
        </div>
      )}
      <h4>Plans</h4>
      <div className="plan-grid">
        {(data?.plans || []).map((p) => (
          <div key={p.name} className={`plan ${p.popular ? "popular" : ""}`}>
            {p.popular && <em>Most popular</em>}
            <b>{p.name}</b><span className="plan-credits">{p.credits} credits</span>
            <span className="muted small">{p.unlocks} interview unlocks · {p.blurb}</span>
            <button className={p.popular ? "primary" : ""} onClick={() => { setUpgrade(p); setAmount(String(p.credits)); }}>Upgrade to {p.name}</button>
          </div>
        ))}
      </div>
      {upgrade && (
        <div className="modal" role="dialog" aria-label="Upgrade credits">
          <form className="card" onSubmit={grant}>
            <div className="row between"><h3>{upgrade.name ? `Upgrade to ${upgrade.name}` : "Add credits"}</h3>
              <button type="button" onClick={() => setUpgrade(null)}>Close</button></div>
            {isAdmin ? (
              <>
                <p className="muted small">No payment gateway is connected yet, so this adds the credits manually and records it in the history below.</p>
                <label>Credits<input type="number" min="1" value={amount} onChange={(e) => setAmount(e.target.value)} required /></label>
                <label>Note (e.g. invoice number)<input value={note} onChange={(e) => setNote(e.target.value)} /></label>
                {msg && <p className="error">{msg}</p>}
                <button className="primary">Add credits</button>
              </>
            ) : <p>Only an administrator can add credits. Please ask your admin to upgrade{upgrade.name ? ` to ${upgrade.name}` : ""}.</p>}
          </form>
        </div>
      )}
      <h4>History</h4>
      <table>
        <thead><tr><th>When</th><th>Type</th><th>Credits</th><th>Balance</th><th>Interview</th><th>Note</th></tr></thead>
        <tbody>{(data?.ledger || []).map((r) => (
          <tr key={r.id}><td>{fmt(r.created_at)}</td><td>{label(r.reason)}</td><td>{r.delta > 0 ? `+${r.delta}` : r.delta}</td>
            <td>{r.balance_after}</td><td>{r.interview_id ? `#${r.interview_id}` : "-"}</td><td>{r.note || ""}</td></tr>
        ))}</tbody>
      </table>
    </>
  );
}

// ---------------------------------------------------------------- notifications + admins
function Notifications() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const { data, error } = useLoad(() => api.notifications({ status, page, page_size: 25 }), JSON.stringify([status, page]));
  return (
    <>
      <div className="filters">
        <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
          <option value="">All</option>{["sent", "failed", "skipped", "queued"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      <table>
        <thead><tr><th>When</th><th>Candidate #</th><th>Channel</th><th>Kind</th><th>Status</th><th>Detail</th></tr></thead>
        <tbody>
          {(data?.items || []).map((n) => (
            <tr key={n.id}><td>{fmt(n.created_at)}</td><td>{n.candidate_id}</td><td>{n.channel}</td><td>{label(n.kind)}</td>
              <td><Badge value={n.status} /></td><td className="muted small">{n.error || n.provider || ""}</td></tr>
          ))}
        </tbody>
      </table>
      {data && <Pager page={page} total={data.total} size={25} onPage={setPage} />}
    </>
  );
}

function Admins() {
  const { data, error, reload } = useLoad(api.admins, JSON.stringify([]));
  const [f, setF] = useState({ email: "", password: "", full_name: "", role: "recruiter" });
  const [msg, setMsg] = useState("");
  async function create(e) {
    e.preventDefault();
    try { await api.createAdmin(f); setF({ email: "", password: "", full_name: "", role: "recruiter" }); setMsg("Created"); reload(); }
    catch (err) { setMsg(err.message); }
  }
  return (
    <>
      {error && <p className="error">{error}</p>}
      <table>
        <thead><tr><th>E-mail</th><th>Name</th><th>Role</th><th>Active</th><th>Last sign-in</th><th /></tr></thead>
        <tbody>{(data || []).map((a) => (
          <tr key={a.id}><td>{a.email}</td><td>{a.full_name}</td><td>{a.role}</td><td>{a.is_active ? "yes" : "no"}</td><td>{fmt(a.last_login_at)}</td>
            <td><button onClick={() => api.updateAdmin(a.id, { is_active: !a.is_active }).then(reload, (e) => setMsg(e.message))}>{a.is_active ? "Deactivate" : "Activate"}</button></td></tr>
        ))}</tbody>
      </table>
      <form className="card" onSubmit={create}>
        <h3>Add admin / recruiter</h3>
        <div className="row wrap">
          <input placeholder="E-mail" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required />
          <input placeholder="Name" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} />
          <input placeholder="Password (10+ chars)" type="password" autoComplete="new-password" minLength="10" maxLength="72"
            value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required />
          <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option>recruiter</option><option>admin</option></select>
          <button className="primary">Add</button>
        </div>
        {msg && <p className="notice">{msg}</p>}
      </form>
    </>
  );
}

// ---------------------------------------------------------------- shell
export default function AdminApp() {
  const [signedIn, setSignedIn] = useState(isSignedIn());
  const [tab, setTab] = useState("dashboard");
  const [interviewJob, setInterviewJob] = useState("");
  const [jobs, setJobs] = useState([]);
  const admin = getAdmin();

  useEffect(() => setUnauthorizedHandler(() => setSignedIn(false)), []);
  const loadJobs = useCallback(() => api.jobs({ page_size: 100 }).then((d) => setJobs(d.items), () => {}), []);
  useEffect(() => { if (signedIn) loadJobs(); }, [signedIn, loadJobs]);

  // Anything under /admin that is not signed in shows the login screen (the backend enforces access regardless).
  if (!signedIn) return <Login onSignedIn={() => setSignedIn(true)} />;

  const tabs = [["dashboard", "Dashboard"], ["candidates", "Candidates"], ["jobs", "Jobs"],
    ["interviews", "Interviews"], ["credits", "Credits"], ["notifications", "Notifications"],
    ...(admin?.role === "admin" ? [["admins", "Admins"]] : [])];
  return (
    <div className="admin">
      <header>
        <strong>Interview Platform · Admin</strong>
        <nav>{tabs.map(([k, l]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => { if (k === "interviews") setInterviewJob(""); setTab(k); }}>{l}</button>)}</nav>
        <span className="muted">{admin?.email} ({admin?.role})</span>
        <button onClick={() => logout().then(() => { setSignedIn(false); window.history.replaceState({}, "", "/admin/login"); })}>Sign out</button>
      </header>
      <main>
        {tab === "dashboard" && <Dashboard />}
        {tab === "candidates" && <Candidates jobs={jobs} />}
        {tab === "jobs" && <Jobs onChanged={loadJobs} onViewInterviews={(id) => { setInterviewJob(id); setTab("interviews"); }} />}
        {tab === "interviews" && <Interviews key={interviewJob} jobs={jobs} initialJobId={interviewJob} />}
        {tab === "credits" && <Credits />}
        {tab === "notifications" && <Notifications />}
        {tab === "admins" && <Admins />}
      </main>
    </div>
  );
}
