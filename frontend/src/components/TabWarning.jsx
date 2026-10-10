// Full-width banner for the tab-switch rule. `info` is { count, warningsLeft, limit } for a warning,
// or { ended: true } once the backend ended the interview.
export default function TabWarning({ info, onClose }) {
  if (!info) return null;

  if (info.ended) {
    return (
      <div className="tab-warning is-ended" role="alert">
        <strong>The interview was ended.</strong>
        <span> You left this page too many times. Your answers so far were saved.</span>
      </div>
    );
  }

  return (
    <div className="tab-warning" role="alert">
      <strong>
        Warning {info.count} of {info.limit}
      </strong>
      <span>
        {" "}
        Please stay on this page. If you switch tabs or windows {info.warningsLeft === 0 ? "once more" : "again"}
        {info.warningsLeft > 1 ? ` (${info.warningsLeft} warnings left)` : ""}, the interview will end.
      </span>
      <button type="button" onClick={onClose}>
        I understand
      </button>
    </div>
  );
}
