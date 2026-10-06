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
function InterviewDetail({ id, onClose }) {
  const { data: i, error, reload } = useLoad(() => api.interview(id), JSON.stringify([id]));
  const [msg, setMsg] = useState("");
  if (error) return <div className="modal"><div className="card"><button onClick={onClose}>Close</button><p className="error">{error}</p></div></div>;
  if (!i) return null;
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
          <button onClick={() => api.evaluate(i.id).then(() => { setMsg("Evaluation updated"); reload(); }, (e) => setMsg(e.message))}>Re-run AI evaluation</button>
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
        {i.summary?.overview && <section><h3>Summary</h3><p>{i.summary.overview}</p></section>}
        {i.events.length > 0 && (
          <section><h3>Recording events</h3>
            <ul>{i.events.map((e, k) => <li key={k} className="small">{e.type} at {Math.round((e.offset_ms || 0) / 1000)}s</li>)}</ul>
          </section>
        )}
        <section>
          <h3>Transcript</h3>
          <div className="transcript">
            {i.transcript.map((t, k) => (
              <p key={k} className={t.role}><b>{t.role === "assistant" ? "Interviewer" : "Candidate"}:</b> {t.text}</p>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Interviews() {
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState(null);
  const { data, error } = useLoad(() => api.interviews({ status, page, page_size: 25 }), JSON.stringify([status, page]));
  return (
    <>
      <div className="filters">
        <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
          <option value="">All statuses</option>
          {["running", "finished", "ended_early", "failed"].map((s) => <option key={s} value={s}>{label(s)}</option>)}
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      <table>
        <thead><tr><th>#</th><th>Candidate</th><th>Attempt</th><th>Status</th><th>AI score</th><th>Recommendation</th><th>Started</th></tr></thead>
        <tbody>
          {(data?.items || []).map((i) => (
            <tr key={i.id} className="click" onClick={() => setOpen(i.id)}>
              <td>{i.id}</td><td>{i.candidate_name}</td><td>{i.attempt_number}</td>
              <td><Badge value={i.status} /></td>
              <td>{i.overall_score == null ? "-" : Math.round(i.overall_score)}</td>
              <td>{i.recommendation ? <Badge value={i.recommendation} /> : "-"}</td>
              <td>{fmt(i.started_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && <Pager page={page} total={data.total} size={25} onPage={setPage} />}
      {open && <InterviewDetail id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

// ---------------------------------------------------------------- jobs
function JobForm({ job, onDone, onCancel }) {
  const [f, setF] = useState({
    title: job?.title || "", description: job?.description || "", department: job?.department || "",
    location: job?.location || "", employment_type: job?.employment_type || "",
    required_skills: (job?.required_skills || []).join(", "), status: job?.status || "open",
  });
  const [error, setError] = useState("");
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  async function save(e) {
    e.preventDefault();
    setError("");
    const body = { ...f, required_skills: f.required_skills.split(",").map((s) => s.trim()).filter(Boolean) };
    try {
      if (job) await api.updateJob(job.id, body); else await api.createJob(body);
      onDone();
    } catch (err) { setError(err.message); }
  }
  return (
    <form className="card" onSubmit={save}>
      <h3>{job ? "Edit job" : "New job"}</h3>
      <label>Title<input value={f.title} onChange={set("title")} required /></label>
      <label>Job description (the AI interviewer reads this)
        <textarea rows="7" value={f.description} onChange={set("description")} required minLength="10" /></label>
      <div className="row wrap">
        <label>Department<input value={f.department} onChange={set("department")} /></label>
        <label>Location<input value={f.location} onChange={set("location")} /></label>
        <label>Type<input value={f.employment_type} onChange={set("employment_type")} placeholder="full_time" /></label>
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

function Jobs({ onChanged }) {
  const { data, error, reload } = useLoad(() => api.jobs({ page_size: 100 }), JSON.stringify([]));
  const [editing, setEditing] = useState(null);   // null | "new" | job
  const done = () => { setEditing(null); reload(); onChanged(); };
  return (
    <>
      <div className="row between"><h3>Jobs</h3><button className="primary" onClick={() => setEditing("new")}>New job</button></div>
      {error && <p className="error">{error}</p>}
      {editing && <JobForm job={editing === "new" ? null : editing} onDone={done} onCancel={() => setEditing(null)} />}
      <table>
        <thead><tr><th>Title</th><th>Location</th><th>Status</th><th>Candidates</th><th /></tr></thead>
        <tbody>
          {(data?.items || []).map((j) => (
            <tr key={j.id}>
              <td>{j.title}</td><td>{j.location || "-"}</td><td><Badge value={j.status} /></td><td>{j.candidate_count}</td>
              <td className="row">
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
  const [jobs, setJobs] = useState([]);
  const admin = getAdmin();

  useEffect(() => setUnauthorizedHandler(() => setSignedIn(false)), []);
  const loadJobs = useCallback(() => api.jobs({ page_size: 100 }).then((d) => setJobs(d.items), () => {}), []);
  useEffect(() => { if (signedIn) loadJobs(); }, [signedIn, loadJobs]);

  // Anything under /admin that is not signed in shows the login screen (the backend enforces access regardless).
  if (!signedIn) return <Login onSignedIn={() => setSignedIn(true)} />;

  const tabs = [["dashboard", "Dashboard"], ["candidates", "Candidates"], ["jobs", "Jobs"],
    ["interviews", "Interviews"], ["notifications", "Notifications"],
    ...(admin?.role === "admin" ? [["admins", "Admins"]] : [])];
  return (
    <div className="admin">
      <header>
        <strong>Interview Platform · Admin</strong>
        <nav>{tabs.map(([k, l]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>)}</nav>
        <span className="muted">{admin?.email} ({admin?.role})</span>
        <button onClick={() => logout().then(() => { setSignedIn(false); window.history.replaceState({}, "", "/admin/login"); })}>Sign out</button>
      </header>
      <main>
        {tab === "dashboard" && <Dashboard />}
        {tab === "candidates" && <Candidates jobs={jobs} />}
        {tab === "jobs" && <Jobs onChanged={loadJobs} />}
        {tab === "interviews" && <Interviews />}
        {tab === "notifications" && <Notifications />}
        {tab === "admins" && <Admins />}
      </main>
    </div>
  );
}
