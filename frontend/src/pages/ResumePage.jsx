import { useState } from "react";
import ResumeUpload from "../components/ResumeUpload";
import { uploadResume } from "../services/api";

function ResumePage({ onResumeUploaded, config, consentVersion }) {
  const [name, setName] = useState("");
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

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

      const result = await uploadResume(name.trim(), file, consentVersion);

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
          <span className="room-logo">AI</span>
          <div>
            <strong>AI Interview</strong>
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
