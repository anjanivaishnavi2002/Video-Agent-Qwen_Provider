function Welcomepage({ onStart, config }) {
  const steps = [
    {
      title: "Upload your resume",
      text: "Your interview is personalised from your own experience.",
    },
    {
      title: "Talk naturally",
      text: "Just speak. There are no buttons to hold or press.",
    },
    {
      title: "Finish and relax",
      text: `Takes about ${config.interview_minutes} minutes. The team reviews it afterwards.`,
    },
  ];

  const tips = [
    "Use Chrome or Edge on a laptop or desktop",
    "Sit in a quiet room with your face well lit",
    "Allow camera and microphone when asked",
  ];

  return (
    <div className="page lobby">
      <div className="lobby-card lobby-wide">
        <div className="lobby-brand">
          <span className="room-logo">Hello</span>
          <div>
            <strong>Interview</strong>
            <span>{config.interview_type}</span>
          </div>
        </div>

        <h1>Meet {config.interviewer_name}, your interviewer</h1>

        <p className="lobby-lead">
          Have a natural conversation about your experience. Your interviewer
          listens, asks follow-up questions and adapts to what you say.
        </p>

        <ol className="lobby-steps">
          {steps.map((step, index) => (
            <li key={step.title}>
              <span className="step-number">{index + 1}</span>
              <div>
                <strong>{step.title}</strong>
                <span>{step.text}</span>
              </div>
            </li>
          ))}
        </ol>

        <ul className="lobby-tips">
          {tips.map((tip) => (
            <li key={tip}>{tip}</li>
          ))}
        </ul>

        <button className="lobby-button" onClick={onStart}>
          Get started
        </button>
      </div>
    </div>
  );
}

export default Welcomepage;
