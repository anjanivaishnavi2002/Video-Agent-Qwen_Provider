import { useEffect, useRef, useState } from "react";

function wordCount(text) {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

export function EmailTask({ task, drafts, selected, onSelect, onChange, onSave, saveState }) {
  const emails = task.emails?.length ? task.emails
    : [{ id: "e1", from_name: "Customer", subject: task.title, body: task.scenario }];
  const mail = emails.find((m) => m.id === selected) || emails[0];
  const draft = drafts[mail.id] || { subject: "", body: "" };
  const answered = emails.filter((m) => (drafts[m.id]?.body || "").trim()).length;
  return (
    <div className="task-pane email-inbox">
      <p className="task-instructions">{task.instructions}</p>
      <div className="inbox-layout">
        <ul className="inbox-list" aria-label="Inbox">
          {emails.map((m) => (
            <li key={m.id}>
              <button type="button" className={`inbox-item ${m.id === mail.id ? "active" : ""}`} onClick={() => onSelect(m.id)}>
                <strong>{m.from_name}</strong>
                <span>{m.subject}</span>
                {(drafts[m.id]?.body || "").trim() && <em className="chip done">Replied</em>}
              </button>
            </li>
          ))}
        </ul>
        <div className="inbox-reading">
          <div className="customer-email">
            <b>{mail.from_name}</b> <span className="muted">- {mail.subject}</span>
            <p>{mail.body}</p>
          </div>
          <label className="field">
            <span>Your reply - subject</span>
            <input type="text" value={draft.subject} maxLength={200}
              onChange={(e) => onChange(mail.id, { ...draft, subject: e.target.value })} placeholder={`Re: ${mail.subject}`} />
          </label>
          <label className="field">
            <span>Your reply</span>
            <textarea rows={8} value={draft.body} maxLength={6000}
              onChange={(e) => onChange(mail.id, { ...draft, body: e.target.value })} placeholder="Write your reply here..." />
          </label>
          <div className="task-actions">
            <span className="hint">{wordCount(draft.body)} words</span>
            <span className="hint">{answered} of {emails.length} answered</span>
            <span className="hint">{saveState === "saving" ? "Saving..." : saveState === "saved" ? "Saved" : ""}</span>
            <button type="button" onClick={() => onSave(mail.id)}>Save draft</button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ChatTask({ task, messages, onSend, waiting, error }) {
  const [text, setText] = useState("");
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, waiting]);

  const submit = (event) => {
    event.preventDefault();
    const value = text.trim();
    if (!value || waiting) return;
    setText("");
    onSend(value);
  };

  return (
    <div className="task-pane">
      <p className="task-scenario">{task.scenario}</p>
      <p className="task-instructions">{task.instructions}</p>

      <div className="chat-box" aria-live="polite">
        <div className="chat-msg customer">
          <b>{task.customer_name || "Customer"}</b>
          <p>{task.opening_message}</p>
        </div>
        {messages.map((m, i) => (
          <div key={`${m.role}-${i}`} className={`chat-msg ${m.role === "agent" ? "agent" : "customer"}`}>
            <b>{m.role === "agent" ? "You" : task.customer_name || "Customer"}</b>
            <p>{m.text}</p>
          </div>
        ))}
        {waiting && <p className="hint">{task.customer_name || "The customer"} is typing...</p>}
        <div ref={endRef} />
      </div>

      {error && <p className="error">{error}</p>}
      <form className="chat-input" onSubmit={submit}>
        <input
          type="text"
          value={text}
          maxLength={2000}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type your reply to the customer"
          disabled={waiting}
        />
        <button type="submit" disabled={waiting || !text.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}

