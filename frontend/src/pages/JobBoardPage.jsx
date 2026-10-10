import { useCallback, useEffect, useMemo, useState } from "react";
import PortalSettings from "../components/PortalSettings";
import { getAccount, listApplications, listJobs, signOut } from "../services/portalApi";

const STATUS = {
  applied: "Applied", invited: "Invited", in_progress: "In progress", completed: "Interview done",
  shortlisted: "Shortlisted", rejected: "Not selected", on_hold: "On hold",
};
const PROCESS = { voice: "Voice", chat: "Chat support", email: "Email support", blended: "Blended", back_office: "Back office", other: "Other" };
const BANDS = [
  ["", "Any experience"], ["0-1", "Fresher (0-1 yrs)"], ["1-3", "1-3 years"], ["3-5", "3-5 years"], ["5-8", "5-8 years"], ["8-99", "8+ years"],
];

const expLabel = (j) => (j.experience_min == null && j.experience_max == null ? "Any experience"
  : j.experience_max == null ? `${j.experience_min}+ yrs` : j.experience_min === 0 && j.experience_max <= 1 ? "Fresher"
  : `${j.experience_min ?? 0}-${j.experience_max} yrs`);

function matchesBand(job, band) {
  if (!band) return true;
  const [lo, hi] = band.split("-").map(Number);
  const jl = job.experience_min ?? 0, jh = job.experience_max ?? 99;
  return jl <= hi && jh >= lo;
}

// Production-style careers portal: search + filters, job cards, a detail panel, my applications, and settings.
export default function JobBoardPage({ onApply, onStart, onSignedOut, notice, onDismissNotice }) {
  const [jobs, setJobs] = useState(null);
  const [apps, setApps] = useState([]);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const [tab, setTab] = useState("jobs");
  const [q, setQ] = useState("");
  const [band, setBand] = useState("");
  const [process, setProcess] = useState("");
  const [location, setLocation] = useState("");
  const account = getAccount();

  const load = useCallback(() => {
    Promise.all([listJobs(), listApplications()])
      .then(([j, a]) => { setJobs(j); setApps(a); })
      .catch((err) => { setError(err.message); if (/sign in again/i.test(err.message)) onSignedOut(); });
  }, [onSignedOut]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (tab !== "settings") load(); }, [tab, load]);

  const locations = useMemo(() => [...new Set((jobs || []).map((j) => j.location).filter(Boolean))].sort(), [jobs]);
  const shown = useMemo(() => (jobs || []).filter((j) => {
    const text = `${j.title} ${j.department || ""} ${(j.required_skills || []).join(" ")}`.toLowerCase();
    return (!q || text.includes(q.toLowerCase())) && matchesBand(j, band)
      && (!process || j.process_type === process) && (!location || j.location === location);
  }), [jobs, q, band, process, location]);

  const job = jobs?.find((j) => j.id === selected);
  const appFor = (jobId) => apps.find((a) => a.job?.id === jobId);
  const needsInterview = apps.length > 0 && apps.every((a) => !a.has_interviews);

  return (
    <div className="portal">
      <header className="portal-top">
        <strong>Careers portal</strong>
        <nav>
          <button className={tab === "jobs" ? "active" : ""} onClick={() => setTab("jobs")}>Open jobs {jobs ? `(${jobs.length})` : ""}</button>
          <button className={tab === "apps" ? "active" : ""} onClick={() => setTab("apps")}>My applications ({apps.length})</button>
          <button className={tab === "settings" ? "active" : ""} onClick={() => setTab("settings")}>Settings</button>
        </nav>
        <span className="muted">{account?.full_name}</span>
        <button onClick={() => signOut().then(onSignedOut)}>Sign out</button>
      </header>

      <main className="portal-main">
        {notice && <p className="notice" role="status">{notice} <button onClick={onDismissNotice}>Dismiss</button></p>}
        {error && <p className="error">{error}</p>}
        {needsInterview && tab !== "settings" && (
          <p className="notice" role="status">You have applied but not recorded your interview yet. Recruiters see only applications with an interview.{" "}
            <button onClick={() => onStart(apps.find((a) => a.can_start) || apps[0])}>Start your interview</button></p>
        )}
        {!jobs && !error && <p className="hint">Loading jobs...</p>}

        {tab === "jobs" && jobs && (
          <>
            <div className="job-filters">
              <input type="search" placeholder="Search job title, skill or department" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search jobs" />
              <select value={band} onChange={(e) => setBand(e.target.value)} aria-label="Experience">{BANDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
              <select value={process} onChange={(e) => setProcess(e.target.value)} aria-label="Process">
                <option value="">All processes</option>{Object.entries(PROCESS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <select value={location} onChange={(e) => setLocation(e.target.value)} aria-label="Location">
                <option value="">All locations</option>{locations.map((l) => <option key={l}>{l}</option>)}
              </select>
              <span className="muted small">{shown.length} job{shown.length === 1 ? "" : "s"}</span>
            </div>
            <div className="job-layout">
              <ul className="job-list">
                {shown.length === 0 && <li className="hint">No jobs match your filters.</li>}
                {shown.map((j) => {
                  const a = appFor(j.id);
                  return (
                    <li key={j.id}>
                      <button className={`job-card ${selected === j.id ? "active" : ""}`} onClick={() => setSelected(j.id)}>
                        <strong>{j.title}</strong>
                        <span>{[j.department, j.location].filter(Boolean).join(" · ") || "Open position"}</span>
                        <span className="skill-chips">
                          {j.process_type && <span className="chip">{PROCESS[j.process_type] || j.process_type}</span>}
                          <span className="chip">{expLabel(j)}</span>
                          {(j.required_skills || []).slice(0, 2).map((s) => <span key={s} className="chip subtle">{s}</span>)}
                        </span>
                        {a && <em className="chip done">{STATUS[a.status] || "Applied"}</em>}
                      </button>
                    </li>
                  );
                })}
              </ul>
              <section className="job-detail">
                {!job && <p className="hint">Select a job to read the description and apply.</p>}
                {job && (
                  <>
                    <h2>{job.title}</h2>
                    <p className="muted">{[job.department, job.location, job.employment_type?.replaceAll("_", " ")].filter(Boolean).join(" · ")}</p>
                    <div className="skill-chips">
                      {job.process_type && <span className="chip">{PROCESS[job.process_type] || job.process_type}</span>}
                      <span className="chip">{expLabel(job)}</span>
                    </div>
                    {job.required_skills.length > 0 && (
                      <>
                        <h3>Skills</h3>
                        <div className="skill-chips">{job.required_skills.map((s) => <span key={s} className="chip">{s}</span>)}</div>
                      </>
                    )}
                    <h3>About the role</h3>
                    <p className="jd-text">{job.description}</p>
                    {appFor(job.id) ? (
                      appFor(job.id).interview_attached ? (
                        <p className="chip done">Applied - interview #{appFor(job.id).interview_id} is attached</p>
                      ) : (
                        <button className="lobby-button" disabled={!appFor(job.id).can_start} onClick={() => onStart(appFor(job.id))}>
                          Start your interview
                        </button>
                      )
                    ) : (
                      <button className="lobby-button" onClick={() => onApply(job)}>
                        {apps.some((a) => a.has_interviews) ? "Apply (your latest interview is attached)" : "Apply and take the interview"}
                      </button>
                    )}
                  </>
                )}
              </section>
            </div>
          </>
        )}

        {tab === "apps" && (
          <ul className="app-list">
            {apps.length === 0 && <li className="hint">You have not applied to any job yet.</li>}
            {apps.map((a) => (
              <li key={a.candidate_id} className="app-row">
                <div>
                  <strong>{a.job?.title || "Application"}</strong>
                  <span className="muted"> · {STATUS[a.status] || a.status}</span>
                  {a.message && !a.can_start && !a.interview_attached && <div className="field-hint">{a.message}</div>}
                </div>
                {a.interview_attached ? (
                  <span className="chip done">Interview #{a.interview_id} attached</span>
                ) : (
                  <button disabled={!a.can_start} className="lobby-button small" onClick={() => onStart(a)}>Start interview</button>
                )}
              </li>
            ))}
          </ul>
        )}

        {tab === "settings" && <PortalSettings onRecordNew={onStart} />}
      </main>
    </div>
  );
}
