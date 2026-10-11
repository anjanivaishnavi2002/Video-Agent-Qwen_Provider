import { useEffect, useState } from "react";

// Candidate-facing notices from the camera checks. They are gentle reminders only; the interview is never stopped
// by them and a person (not software) reviews anything that was flagged.
const MESSAGES = {
  face_missing: "We can't see your face. Please sit in front of the camera so you stay in view.",
  multiple_faces: "Only you should be in view during the interview. Please ask anyone else to step out of the frame.",
};

export default function CameraNotice({ event, dark }) {
  const [text, setText] = useState("");

  useEffect(() => {
    if (!event) return undefined;
    if (event.type === "face_returned") {
      setText("");
      return undefined;
    }
    const message = MESSAGES[event.type];
    if (!message) return undefined;
    setText(message);
    if (event.type === "multiple_faces") {
      const timer = setTimeout(() => setText(""), 10000);   // face_missing stays until the face is back
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [event]);

  const shown = text || (dark ? "It looks quite dark. Please face a light so your camera can see you clearly." : "");
  if (!shown) return null;
  return (
    <div className="camera-notice" role="status">
      {shown}
    </div>
  );
}
