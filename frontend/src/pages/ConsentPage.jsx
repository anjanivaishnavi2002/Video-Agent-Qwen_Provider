import { useEffect, useState } from "react";
import { getConsent } from "../services/api";

// Shown before ANY data is collected. The wording comes from the backend
// (backend/app/prompts/consent.yaml), so it can be changed without touching code.
function ConsentPage({ onAccept, onDecline }) {
  const [form, setForm] = useState(null);
  const [error, setError] = useState("");
  const [checked, setChecked] = useState({});

  useEffect(() => {
    let cancelled = false;

    getConsent()
      .then((data) => {
        if (!cancelled) setForm(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const allChecked =
    form !== null &&
    form.statements.every((_, index) => checked[index] === true);

  return (
    <div className="page lobby">
      <div className="lobby-card lobby-wide consent-card">
        <div className="lobby-brand">
          <span className="room-logo">AI</span>
          <div>
            <strong>AI Interview</strong>
            <span>Step 1 of 2 · Consent</span>
          </div>
        </div>

        {error && <p className="error">{error}</p>}
        {!form && !error && <p className="field-hint">Loading...</p>}

        {form && (
          <>
            <h1>{form.title}</h1>
            <p className="lobby-lead">{form.intro}</p>

            <div className="consent-scroll" tabIndex={0}>
              {form.sections.map((section) => (
                <section key={section.heading}>
                  <h3>{section.heading}</h3>
                  {section.bullets.length > 0 && (
                    <ul>
                      {section.bullets.map((text) => (
                        <li key={text}>{text}</li>
                      ))}
                    </ul>
                  )}
                  {section.body.map((text) => (
                    <p key={text}>{text}</p>
                  ))}
                </section>
              ))}
            </div>

            <div className="consent-statements">
              {form.statements.map((statement, index) => (
                <label className="consent" key={statement}>
                  <input
                    type="checkbox"
                    checked={checked[index] === true}
                    onChange={(event) =>
                      setChecked((prev) => ({
                        ...prev,
                        [index]: event.target.checked,
                      }))
                    }
                  />
                  <span>{statement}</span>
                </label>
              ))}
            </div>

            <div className="consent-actions">
              <button
                type="button"
                className="consent-decline"
                onClick={onDecline}
              >
                Decline
              </button>
              <button
                type="button"
                className="lobby-button"
                disabled={!allChecked}
                onClick={() => onAccept(form.version)}
              >
                I agree, continue
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default ConsentPage;
