import { useEffect, useState } from "react";
import ResumeUpload from "../components/ResumeUpload";
import { applyToJob, listMyInterviews } from "../services/portalApi";

// Details + resume for ONE job. A returning candidate may reuse the resume they uploaded before.
export default function ApplyPage({ job, config, consentVersion, hasPreviousResume, onApplied, onBack }) {
  const [experienceYears, setExperienceYears] = useState("");
  const [phone, setPhone] = useState("");
  const [location, setLocation] = useState("");
  const [skills, setSkills] = useState((job.required_skills || []).join(", "));
  const [file, setFile] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [interviews, setInterviews] = useState([]);
  const [choice, setChoice] = useState("");        // "" until loaded, then an interview id or "new"
  const allowed = config.resume.allowed_extensions;
  const maxMb = config.resume.max_mb;

  useEffect(() => {
    listMyInterviews().then((r) => {
      const done = r.interviews.filter((i) => i.status !== "running");
      setInterviews(done);
      setChoice(done.length ? String(done[0].id) : "new");
    }, () => setChoice("new"));
  }, []);

  function pick(selected) {
    const ext = `.${selected.name.split(".").pop().toLowerCase()}`;
    if (!allowed.includes(ext)) return setError(`Allowed resume formats: ${allowed.join(", ")}`);
    if (selected.size > maxMb * 1024 * 1024) return setError(`Resume is too large (limit ${maxMb} MB).`);
    setError("");
    setFile(selected);
  }

  async function submit(e) {
    e.preventDefault();
    if (!file && !hasPreviousResume) return setError("Please upload your resume.");
    setBusy(true);
    setError("");
    try {
      onApplied(await applyToJob(job.id, consentVersion, {
        experienceYears, phone, location, skills, file,
        interviewId: choice && choice !== "new" ? choice : null, newInterview: choice === "new",
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <form className="resume-card" onSubmit={submit}>
        <h2>Apply: {job.title}</h2>
        <label>Total experience (years)
          <input type="number" min="0" max="60" step="0.5" value={experienceYears} onChange={(e) => setExperienceYears(e.target.value)} placeholder="0 if you are a fresher" />
        </label>
        <label>Mobile number<input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" /></label>
        <label>Current city<input value={location} onChange={(e) => setLocation(e.target.value)} /></label>
        <label>Your skills (comma separated)<input value={skills} onChange={(e) => setSkills(e.target.value)} /></label>
        <ResumeUpload file={file} setFile={pick} allowedExtensions={allowed} />
        {hasPreviousResume && !file && <p className="field-hint">We will use the resume from your earlier application, or upload a new one.</p>}
        {interviews.length > 0 && (
          <label>Interview to attach to this job
            <select value={choice} onChange={(e) => setChoice(e.target.value)}>
              {interviews.map((i) => (
                <option key={i.id} value={i.id}>Interview #{i.id} - {new Date(i.started_at.endsWith("Z") ? i.started_at : `${i.started_at}Z`).toLocaleString()}</option>
              ))}
              <option value="new">Record a new interview for this job</option>
            </select>
          </label>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="row">
          <button type="button" onClick={onBack}>Back</button>
          <button className="lobby-button" disabled={busy}>{busy ? "Applying..." : "Apply"}</button>
        </div>
      </form>
    </div>
  );
}
