function List({ title, items }) {
  if (!items || items.length === 0) return null;
  return (
    <div className="report-block">
      <h4>{title}</h4>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

// What the CANDIDATE sees: short feedback only. Scores, camera events and the reviewer report stay with the admin.
export default function ReportPanel({ state, report, name }) {
  if (state === "loading") {
    return (
      <main className="report-wrap">
        <section className="report-card">
          <h3>Preparing your feedback…</h3>
          <p className="report-muted">This takes a few seconds. Please keep this page open.</p>
        </section>
      </main>
    );
  }
  if (state === "failed" || !report || report.status === "pending") {
    return (
      <main className="report-wrap">
        <section className="report-card">
          <h3>Thank you{name ? `, ${name}` : ""}</h3>
          <p className="report-muted">
            Your work was saved. The recruiter will review it and be in touch. Your feedback could not be shown
            right now.
          </p>
        </section>
      </main>
    );
  }
  return (
    <main className="report-wrap">
      <header className="report-title">
        <h2>Thank you{name ? `, ${name}` : ""}</h2>
        {report.ended_because && <p className="report-muted">{report.ended_because}</p>}
      </header>

      {report.status === "skipped" && (
        <section className="report-card">
          <p>{report.message || "There was not enough to give feedback on."}</p>
        </section>
      )}

      {report.overview && (
        <section className="report-card">
          <h3>Your feedback</h3>
          <p>{report.overview}</p>
          <List title="What went well" items={report.strengths} />
        </section>
      )}

      {(report.tasks || []).map((task) => (
        <section className="report-card" key={task.title}>
          <h3>{task.title}</h3>
          <p>{task.feedback}</p>
          <List title="How to improve" items={task.improvements} />
        </section>
      ))}

      <section className="report-card">
        <p className="report-muted">The recruiter will review your assessment and contact you about the next steps.</p>
      </section>
    </main>
  );
}
