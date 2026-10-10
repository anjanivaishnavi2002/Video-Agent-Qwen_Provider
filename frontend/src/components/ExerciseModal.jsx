import { useEffect, useRef, useState } from "react";

import { saveChatEmail, sendChatMessage } from "../services/api";
import { ChatTask, EmailTask } from "./TaskPanes";

// A written exercise that the interviewer puts on screen in the middle of the voice interview.
// The interviewer keeps listening; when the candidate presses the button the backend tells the interviewer to ask
// follow-up questions about the work.
export default function ExerciseModal({ task, sessionId, onDone }) {
  const [drafts, setDrafts] = useState({});          // email id -> { subject, body }
  const [selected, setSelected] = useState(task.emails?.[0]?.id || "e1");
  const [saveState, setSaveState] = useState("");
  const [messages, setMessages] = useState([]);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const timer = useRef(null);
  const latest = useRef({});

  useEffect(() => () => clearTimeout(timer.current), []);

  async function saveNow(emailId, value) {
    const draft = value || latest.current[emailId] || { subject: "", body: "" };
    setSaveState("saving");
    try {
      await saveChatEmail(sessionId, task.id, draft.subject, draft.body, emailId);
      setSaveState("saved");
      return true;
    } catch (err) {
      setSaveState("");
      setError(err.message);
      return false;
    }
  }

  function changeDraft(emailId, value) {
    latest.current = { ...latest.current, [emailId]: value };
    setDrafts(latest.current);
    setSaveState("");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => saveNow(emailId, value), 1500);     // autosave, so nothing is lost if the page closes
  }

  async function send(text) {
    setError("");
    setMessages((current) => [...current, { role: "agent", text }]);
    setWaiting(true);
    try {
      const { reply } = await sendChatMessage(sessionId, task.id, text);
      setMessages((current) => [...current, { role: "customer", text: reply }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setWaiting(false);
    }
  }

  async function submit() {
    setSubmitting(true);
    clearTimeout(timer.current);
    if (task.kind === "email") {
      for (const id of Object.keys(latest.current)) {       // save every reply before telling the interviewer
        if (!(await saveNow(id))) {
          setSubmitting(false);
          return;
        }
      }
    }
    onDone(task.id);
  }

  const ready = task.kind === "email"
    ? Object.values(drafts).some((d) => (d.subject + d.body).trim())
    : messages.some((m) => m.role === "agent");

  return (
    <div className="exercise-overlay" role="dialog" aria-modal="true" aria-label="Written exercise">
      <div className="exercise-card">
        <header>
          <span className="eyebrow">{task.kind === "email" ? "Email exercise" : "Chat exercise"}</span>
          <h2>{task.title}</h2>
          <p className="hint">Your interviewer can still hear you. Take your time, then press "Send to interviewer" when you are done. The interviewer waits for you.</p>
        </header>
        {task.kind === "email" ? (
          <EmailTask task={task} drafts={drafts} selected={selected} onSelect={setSelected} onChange={changeDraft}
            onSave={(id) => saveNow(id)} saveState={saveState} />
        ) : (
          <ChatTask task={task} messages={messages} onSend={send} waiting={waiting} error={error} />
        )}
        {task.kind === "email" && error && <p className="error">{error}</p>}
        <footer>
          <button type="button" className="lobby-button" disabled={!ready || submitting || waiting} onClick={submit}>
            {submitting ? "Sending..." : "Send to interviewer"}
          </button>
        </footer>
      </div>
    </div>
  );
}
