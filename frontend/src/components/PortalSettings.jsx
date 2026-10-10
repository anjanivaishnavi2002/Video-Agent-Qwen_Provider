import { useCallback, useEffect, useState } from "react";
import { attachInterview, deleteInterview, getAccount, listMyInterviews } from "../services/portalApi";

const when = (v) => (v ? new Date(v.endsWith?.("Z") ? v : `${v}Z`).toLocaleString() : "-");
const minutes = (s) => (s ? `${Math.max(1, Math.round(s / 60))} min` : "-");

// Settings: my account, my recorded interviews, and which interview each job application uses.
export default function PortalSettings({ onRecordNew }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const account = getAccount();

  const load = useCallback(() => {
    listMyInterviews().then(setData, (e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function run(action) {
    setBusy(true);
    setError("");
    try { await action(); load(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  const finished = (data?.interviews || []).filter((i) => i.status !== "running");

  return (
    <div className="settings">
      {error && <p className="error">{error}</p>}
      <section className="panel">
        <h3>Account</h3>
        <p><strong>{account?.full_name}</strong><br /><span className="muted">{account?.email}</span></p>
      </section>

      <section className="panel">
        <div className="row-between">
          <h3>My interviews <span className="muted small">({finished.length} of {data?.max_interviews ?? 3})</span></h3>
          <button className="lobby-button small" disabled={!data?.can_record_new || busy}
            onClick={() => onRecordNew(data.applications.find((a) => a.can_start) || data.applications[0])}>
            Record a new interview
          </button>
        </div>
        {data && !data.can_record_new && data.message && <p className="field-hint">{data.message}</p>}
        {data && finished.length === 0 && <p className="hint">You have not recorded an interview yet. Apply to a job, then start your interview.</p>}
        <ul className="interview-list">
          {finished.map((i) => (
            <li key={i.id} className="interview-card">
              <div>
                <strong>Interview #{i.id}</strong>
                <span className="muted"> · {when(i.started_at)} · {minutes(i.duration_seconds)}</span>
                {i.tab_switches > 0 && <span className="chip warn">{i.tab_switches} tab switch{i.tab_switches > 1 ? "es" : ""}</span>}
                <div className="skill-chips">
                  {i.attached_to.length === 0 && <span className="muted small">Not attached to any job</span>}
                  {i.attached_to.map((a) => <span key={a.application_id} className="chip done">{a.job_title}</span>)}
                </div>
              </div>
              <button disabled={busy} onClick={() => window.confirm("Delete this recording and its report? Jobs using it switch to your other interview, if any.")
                && run(() => deleteInterview(i.id))}>Delete</button>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel">
        <h3>Interview used for each job</h3>
        <p className="muted small">Recruiters only see the interview you attach to their job.</p>
        {data?.applications.length === 0 && <p className="hint">You have not applied to any job yet.</p>}
        <ul className="app-list">
          {(data?.applications || []).map((a) => (
            <li key={a.candidate_id} className="app-row">
              <div><strong>{a.job?.title || "Application"}</strong>
                <div className="muted small">{a.status.replaceAll("_", " ")}</div></div>
              <select aria-label={`Interview for ${a.job?.title}`} disabled={busy || finished.length === 0}
                value={a.interview_id ?? ""}
                onChange={(e) => run(() => attachInterview(a.candidate_id, e.target.value ? Number(e.target.value) : null))}>
                <option value="">No interview attached</option>
                {finished.map((i) => <option key={i.id} value={i.id}>Interview #{i.id} - {when(i.started_at)}</option>)}
              </select>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
