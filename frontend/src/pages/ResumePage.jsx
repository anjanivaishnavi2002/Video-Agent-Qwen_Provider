import { useEffect, useState } from "react";
import ResumeUpload from "../components/ResumeUpload";
import { getOpenJobs, uploadResume } from "../services/api";

function ResumePage({ onResumeUploaded, config, consentVersion }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [location, setLocation] = useState("");
  const [experienceYears, setExperienceYears] = useState("");
  const [skills, setSkills] = useState("");
  const [jobId, setJobId] = useState("");
  const [jobs, setJobs] = useState([]);
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getOpenJobs().then(setJobs).catch(() => setJobs([]));
  }, []);

  const allowed = config.resume.allowed_extensions;
  const maxMb = config.resume.max_mb;

  // Quick feedback in the browser; the backend enforces the same rules.
  function validateFile(selected) {
    const ext = `.${selected.name.split(".").pop().toLowerCase()}`;

    if (!allowed.includes(ext)) {
      return `Allowed resume formats: ${allowed.join(", ")}`;
    }

    if (selected.size > maxMb * 1024 * 1024) {
      return `Resume is too large (limit ${maxMb} MB).`;
    }

    return "";
  }

  async function handleUpload() {
    if (!name.trim()) {
      setError("Please enter your name.");
      return;
    }

    if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email.trim())) {
      setError("Please enter a valid e-mail address.");
      return;
    }

    if (jobs.length > 0 && !jobId) {
      setError("Please choose the position you are applying for.");
      return;
    }

    if (!file) {
      setError("Please upload your resume.");
      return;
    }

    const problem = validateFile(file);
    if (problem) {
      setError(problem);
      return;
    }

    // Full-screen interview room. Must be requested during the click itself.
    document.documentElement.requestFullscreen?.().catch(() => {});

    try {
      setLoading(true);
      setError("");

      const result = await uploadResume(name.trim(), file, consentVersion, {
        email: email.trim(),
        phone: phone.trim(),
        location: location.trim(),
        experienceYears,
        skills,
        jobId,
      });

      onResumeUploaded(result.candidate_id, name.trim());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page lobby resume-page">
      <div className="lobby-card">
        <div className="lobby-brand">
          <span className="room-logo">Hello</span>
          <div>
            <strong>Interview</strong>
            <span>Step 2 of 2 · Your details</span>
          </div>
        </div>

        <h1>Upload your resume</h1>

        <p className="lobby-lead">
          Your resume is used to personalise the interview.
        </p>

        <label className="field-label" htmlFor="candidate-name">
          Full name
        </label>
        <input
          id="candidate-name"
          type="text"
          placeholder="Your name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />

        <label className="field-label" htmlFor="candidate-email">
          E-mail
        </label>
        <input
          id="candidate-email"
          type="email"
          autoComplete="email"
          placeholder="you@example.com"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />

        <label className="field-label" htmlFor="candidate-phone">
          Phone (optional)
        </label>
        <input
          id="candidate-phone"
          type="tel"
          autoComplete="tel"
          placeholder="+91 98765 43210"
          value={phone}
          onChange={(event) => setPhone(event.target.value)}
        />

        {jobs.length > 0 && (
          <>
            <label className="field-label" htmlFor="candidate-job">
              Position
            </label>
            <select
              id="candidate-job"
              value={jobId}
              onChange={(event) => setJobId(event.target.value)}
            >
              <option value="">Select a position</option>
              {jobs.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.title}
                  {job.location ? ` - ${job.location}` : ""}
                </option>
              ))}
            </select>
          </>
        )}

        <label className="field-label" htmlFor="candidate-location">
          Location (optional)
        </label>
        <input
          id="candidate-location"
          type="text"
          placeholder="City"
          value={location}
          onChange={(event) => setLocation(event.target.value)}
        />

        <label className="field-label" htmlFor="candidate-experience">
          Years of experience (optional)
        </label>
        <input
          id="candidate-experience"
          type="number"
          min="0"
          max="60"
          step="0.5"
          value={experienceYears}
          onChange={(event) => setExperienceYears(event.target.value)}
        />

        <label className="field-label" htmlFor="candidate-skills">
          Key skills (optional, comma separated)
        </label>
        <input
          id="candidate-skills"
          type="text"
          placeholder="Voice process, CRM, English"
          value={skills}
          onChange={(event) => setSkills(event.target.value)}
        />

        <span className="field-label">Resume</span>
        <ResumeUpload
          file={file}
          setFile={setFile}
          allowedExtensions={allowed}
        />
        <p className="field-hint">
          Up to {maxMb} MB. Make sure it is text-based, not a scanned image.
        </p>

        {error && (
          <p className="error">
            {error}
          </p>
        )}

        <button
          className="lobby-button"
          onClick={handleUpload}
          disabled={loading}
        >
          {loading ? "Preparing your interview..." : "Continue"}
        </button>
      </div>
    </div>
  );
}

export default ResumePage;
