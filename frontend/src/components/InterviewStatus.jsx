function InterviewStatus({ status }) {
  const messages = {
    starting: "Starting interview...",
    thinking: "Thinking...",
    speaking: "Speaking...",
    listening: "Listening...",
    finished: "Interview completed",
    error: "Something went wrong",
  };

  return (
    <div className="interview-status">
      <span className="status-dot" />
      <span>
        {messages[status] || "Ready"}
      </span>
    </div>
  );
}

export default InterviewStatus;