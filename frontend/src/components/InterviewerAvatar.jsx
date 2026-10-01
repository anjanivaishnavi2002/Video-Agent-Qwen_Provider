function InterviewerAvatar({ status, name }) {
  return (
    <div
      className={`interviewer-avatar ${
        status === "speaking"
          ? "speaking"
          : ""
      }`}
    >
      <div className="avatar-circle">
        👤
      </div>

      <h2>{name || "AI Interviewer"}</h2>
    </div>
  );
}

export default InterviewerAvatar;
