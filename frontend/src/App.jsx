import { useEffect, useState } from "react";
import "./App.css";

import ConsentPage from "./pages/ConsentPage";
import PortalAuthPage from "./pages/PortalAuthPage";
import JobBoardPage from "./pages/JobBoardPage";
import ApplyPage from "./pages/ApplyPage";
import InterviewPage from "./pages/InterviewPage";
import { getInvitation, getPublicConfig, setInviteToken } from "./services/api";
import { getAccount, isSignedIn, listApplications } from "./services/portalApi";

function getAccountName() {
  return getAccount()?.full_name || "";
}

function App() {
  const [notice, setNotice] = useState("");
  const [page, setPage] = useState(isSignedIn() ? "jobs" : "auth");
  const [candidateId, setCandidateId] = useState(null);
  const [candidateName, setCandidateName] = useState("");
  const [consentVersion, setConsentVersion] = useState(null);
  const [sessionId, setSessionId] = useState(null);
  const [job, setJob] = useState(null);                 // job being applied for / assessed
  const [hasPreviousResume, setHasPreviousResume] = useState(false);

  // All tunable values (silence timeout, resume formats, face thresholds, ...)
  // come from the backend configuration, so there is one place to change them.
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState("");

  // Invitation link (?invite=...): the candidate already applied, so skip the resume step.
  const [invitation, setInvitation] = useState(null);
  const [inviteError, setInviteError] = useState("");

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("invite");
    if (!token) return;
    getInvitation(token)
      .then((data) => {
        setInviteToken(token);
        setInvitation(data);
        if (!data.can_start) setInviteError(data.message || "This interview cannot be started.");
        else setPage("consent");
        // Remove the secret from the address bar / history once it has been captured.
        window.history.replaceState({}, "", window.location.pathname);
      })
      .catch((err) => setInviteError(err.message));
  }, []);

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

  if (inviteError) {
    return (
      <div className="page">
        <div className="resume-card">
          <p className="error">{inviteError}</p>
        </div>
      </div>
    );
  }

  return (
    <>
      {page === "auth" && <PortalAuthPage onSignedIn={() => setPage("jobs")} />}

      {page === "jobs" && (
        <JobBoardPage
          notice={notice}
          onDismissNotice={() => setNotice("")}
          onSignedOut={() => setPage("auth")}
          onApply={(selected) => {
            setJob(selected);
            listApplications().then((a) => setHasPreviousResume(a.length > 0), () => setHasPreviousResume(false));
            setPage("consent");
          }}
          onStart={(application) => {
            setInviteToken(application.invite_token);
            setCandidateId(application.candidate_id);
            setCandidateName(application.name || "");
            setJob(application.job);
            setPage("interview");
          }}
        />
      )}

      {page === "consent" && (
        <ConsentPage
          onAccept={(version) => {
            setConsentVersion(version);
            if (invitation && invitation.can_start) {
              setCandidateId(invitation.candidate_id);
              setCandidateName(invitation.name);
              setPage("interview");
            } else {
              setPage("apply");
            }
          }}
          onDecline={() => setPage(invitation ? "auth" : "jobs")}
        />
      )}

      {page === "apply" && job && (
        <ApplyPage
          job={job}
          config={config}
          consentVersion={consentVersion}
          hasPreviousResume={hasPreviousResume}
          onBack={() => setPage("jobs")}
          onApplied={(application) => {
            if (application.interview_attached) {      // already interviewed once: the recording goes with this job too
              setNotice(`Applied to ${application.job?.title || "the job"}. Your earlier interview is attached - no new interview needed.`);
              setPage("jobs");
              return;
            }
            setInviteToken(application.invite_token);
            setCandidateId(application.candidate_id);
            setCandidateName(getAccountName());
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
