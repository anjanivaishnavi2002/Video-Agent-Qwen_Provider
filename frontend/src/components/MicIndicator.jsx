// Replaces the old hold-to-speak VoiceButton. It is NOT a control: the
// candidate never presses anything. It only shows that the microphone is
// automatically listening, and pulses while speech is being picked up.
function MicIndicator({ listening, speechActive }) {
  if (!listening) {
    return null;
  }

  return (
    <div
      className={`voice-button mic-indicator ${speechActive ? "recording" : ""}`}
      aria-live="polite"
    >
      <span className="mic-icon">🎙️</span>
      <span>{speechActive ? "I'm hearing you" : "Go ahead, I'm listening"}</span>
    </div>
  );
}

export default MicIndicator;
