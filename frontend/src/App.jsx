import { useEffect, useState } from "react";
import "./App.css";

import Welcomepage from "./pages/Welcomepage";
import ConsentPage from "./pages/ConsentPage";
import ResumePage from "./pages/ResumePage";
import InterviewPage from "./pages/InterviewPage";
import { getPublicConfig } from "./services/api";

function App() {
  const [page, setPage] = useState("welcome");
  const [candidateId, setCandidateId] = useState(null);
  const [candidateName, setCandidateName] = useState("");
  const [consentVersion, setConsentVersion] = useState(null);
  const [sessionId, setSessionId] = useState(null);

  // All tunable values (silence timeout, resume formats, face thresholds, ...)
  // come from the backend configuration, so there is one place to change them.
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState("");

  useEffect(() => {
    let cancelled = false;

    getPublicConfig()
      .then((data) => {
        if (!cancelled) setConfig(data);
      })
      .catch((err) => {
        if (!cancelled) setConfigError(err.message);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (configError) {
    return (
      <div className="page">
        <div className="resume-card">
          <p className="error">{configError}</p>
          <button onClick={() => window.location.reload()}>Retry</button>
        </div>
      </div>
    );
  }

  if (!config) {
    return (
      <div className="page">
        <p className="hint">Connecting...</p>
      </div>
    );
  }

  return (
    <>
      {page === "welcome" && (
        <Welcomepage
          config={config}
          onStart={() => setPage("consent")}
        />
      )}

      {page === "consent" && (
        <ConsentPage
          onAccept={(version) => {
            setConsentVersion(version);
            setPage("resume");
          }}
          onDecline={() => setPage("welcome")}
        />
      )}

      {page === "resume" && (
        <ResumePage
          config={config}
          consentVersion={consentVersion}
          onResumeUploaded={(id, name) => {
            setCandidateId(id);
            setCandidateName(name);
            setPage("interview");
          }}
        />
      )}

      {page === "interview" && (
        <InterviewPage
          config={config}
          candidateId={candidateId}
          candidateName={candidateName}
          sessionId={sessionId}
          setSessionId={setSessionId}
        />
      )}
    </>
  );
}

export default App;
