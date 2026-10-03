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

function Scorecard({ card }) {
  if (!card) return null;
  if (card.status !== "ready") {
    return (
      <section className="report-card">
        <h3>Scorecard</h3>
        <p className="report-muted">
          {card.status === "skipped" ? card.reason : card.error || "The scorecard could not be created."}
        </p>
      </section>
    );
  }
  return (
    <section className="report-card">
      <div className="report-head">
        <h3>Scorecard</h3>
        <div className="report-overall" title={card.scale}>
          <strong>{card.overall_score}</strong>
          <span>/ 5 overall</span>
        </div>
      </div>
      <ul className="criteria">
        {card.criteria.map((c) => (
          <li key={c.name}>
            <div className="criterion-row">
              <span>{c.name}</span>
              <b>{c.score} / 5</b>
            </div>
            <div className="bar"><i style={{ width: `${(c.score / 5) * 100}%` }} /></div>
            <p>{c.evidence}</p>
          </li>
        ))}
      </ul>
      <List title="Strengths shown in the answers" items={card.strengths} />
      <List title="Worth probing further" items={card.areas_to_probe} />
      <p className="report-muted">{card.note}</p>
    </section>
  );
}

function Recording({ recording }) {
  if (!recording) return null;
  const events = recording.events || {};
  const counts = events.counts || {};
  const labels = {
    face_missing: "Face not in view",
    face_returned: "Face back in view",
    multiple_faces: "More than one face in view",
    head_movement: "Head movement",
  };
  const rows = Object.entries(counts);
  return (
    <section className="report-card">
      <h3>Recording and camera events</h3>
      <p>Recording saved: {recording.uploaded ? "yes" : "no"}</p>
      {rows.length === 0 ? (
        <p className="report-muted">No camera events were recorded.</p>
      ) : (
        <ul>
          {rows.map(([type, count]) => (
            <li key={type}>{labels[type] || type}: {count}</li>
          ))}
          <li>Total time with no face in view: {events.face_missing_seconds ?? 0} s</li>
        </ul>
      )}
      <p className="report-muted">{events.note}</p>
    </section>
  );
}

export default function ReportPanel({ state, report, name }) {
  if (state === "loading") {
    return (
      <main className="report-wrap">
        <section className="report-card">
          <h3>Preparing the interview report…</h3>
          <p className="report-muted">This takes a few seconds. Please keep this page open.</p>
        </section>
      </main>
    );
  }
  if (state === "failed" || !report) {
    return (
      <main className="report-wrap">
        <section className="report-card">
          <h3>Interview finished</h3>
          <p className="report-muted">Your answers were saved, but the report could not be created right now.</p>
        </section>
      </main>
    );
  }
  const summary = report.interview;
  return (
    <main className="report-wrap">
      <header className="report-title">
        <h2>Interview report{name ? ` - ${name}` : ""}</h2>
        <p className="report-muted">For the reviewer. Based only on the transcript and observable camera events.</p>
      </header>

      {report.status === "skipped" && (
        <section className="report-card"><p>{report.reason}</p></section>
      )}
      {report.status === "failed" && (
        <section className="report-card"><p>The summary failed: {report.error}</p></section>
      )}

      {summary && (
        <section className="report-card">
          <h3>Summary</h3>
          <p>{summary.overview}</p>
          <List title="Topics discussed" items={summary.topics_discussed} />
          <List title="Experience the candidate described" items={summary.stated_experience} />
          <List title="Unanswered or unclear" items={summary.unanswered_or_unclear} />
          <List title="Possible follow-up topics" items={summary.follow_up_topics} />
        </section>
      )}

      <Scorecard card={report.scorecard} />
      <Recording recording={report.recording} />
    </main>
  );
}
