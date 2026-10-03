"""
Dynamic interview engine.

There is no question list and no fixed sequence here. Every turn the model
receives structured context (candidate, resume profile, conversation, latest
answer, interview settings, state) and decides for itself what to explore,
whether to follow up, move on, pose a situational question, or end.
"""
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime

from app.config import settings
from app.prompts.interviewer import build_system_prompt, load_prompts, render
from app.providers.ollama_provider import OllamaProvider, get_llm
from app.providers.llm_errors import LLMResponseError

logger = logging.getLogger(__name__)

TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "learned": {"type": "string"},
        "topic": {"type": "string"},
        "move": {
            "type": "string",
            "enum": ["opening", "follow_up", "new_resume_topic", "situational", "clarify", "closing"],
        },
        "end_interview": {"type": "boolean"},
        "spoken_text": {"type": "string"},
    },
    "required": ["learned", "topic", "move", "end_interview", "spoken_text"],
}


@dataclass
class InterviewSettings:
    """Everything configurable about one interview. Built from central config."""

    interviewer_name: str
    tone: str
    interview_type: str
    duration_minutes: int
    max_turns: int
    min_turns_before_end: int
    history_turns: int
    max_learned_facts: int
    max_empty_streak: int
    empty_before_reprompt: int
    llm_model: str
    temperature: float

    @classmethod
    def from_config(cls) -> "InterviewSettings":
        return cls(
            interviewer_name=settings.INTERVIEWER_NAME,
            tone=settings.INTERVIEWER_TONE,
            interview_type=settings.INTERVIEW_TYPE,
            duration_minutes=settings.INTERVIEW_DURATION_MINUTES,
            max_turns=settings.MAX_TURNS,
            min_turns_before_end=settings.MIN_TURNS_BEFORE_END,
            history_turns=settings.HISTORY_TURNS_IN_PROMPT,
            max_learned_facts=settings.MAX_LEARNED_FACTS,
            max_empty_streak=settings.MAX_EMPTY_STREAK,
            empty_before_reprompt=settings.EMPTY_BEFORE_REPROMPT,
            llm_model=settings.active_model,
            temperature=settings.LLM_TEMPERATURE,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "InterviewSettings":
        base = cls.from_config()
        if not data:
            return base
        fields = {k: v for k, v in data.items() if k in base.__dataclass_fields__}
        return cls(**{**asdict(base), **fields})


class InterviewSession:
    def __init__(
        self,
        cfg: InterviewSettings,
        candidate_name: str,
        resume_profile: dict | None,
        resume_text: str | None,
        *,
        llm: OllamaProvider | None = None,
        transcript: list[dict] | None = None,
        started_at: datetime | None = None,
    ):
        """
        resume_text: the resume text to embed verbatim (None -> profile only).
        transcript / started_at: pass to resume a session after a server restart.
        """
        self.cfg = cfg
        self.llm = llm or get_llm()
        self.prompts = load_prompts()
        self.candidate_name = candidate_name
        self.transcript: list[dict] = list(transcript or [])
        self.finished = False
        self.end_reason: str | None = None
        self.empty_streak = 0
        self.lock = threading.Lock()  # one turn at a time

        started = started_at or datetime.utcnow()
        # monotonic-ish start based on wall clock so a restored session keeps its elapsed time
        self._start_epoch = time.time() - (datetime.utcnow() - started).total_seconds()

        self.system_prompt = build_system_prompt(
            interviewer_name=cfg.interviewer_name,
            tone=cfg.tone,
            interview_type=cfg.interview_type,
            candidate_name=candidate_name,
            resume_profile=resume_profile,
            resume_text=resume_text,
        )

    # ------------------------------------------------------------------
    # State derived from the transcript (so it survives a restart)
    # ------------------------------------------------------------------
    @property
    def turns(self) -> int:
        return sum(1 for t in self.transcript if t["role"] == "candidate")

    def minutes_elapsed(self) -> float:
        return (time.time() - self._start_epoch) / 60

    def _ai_turns(self) -> list[dict]:
        return [t for t in self.transcript if t["role"] == "assistant"]

    def _topics(self) -> list[str]:
        seen: list[str] = []
        for t in self._ai_turns():
            topic = (t.get("topic") or "").strip()
            if topic and topic.lower() not in (s.lower() for s in seen):
                seen.append(topic)
        return seen

    def _learned(self) -> list[str]:
        facts = [t["learned"].strip() for t in self._ai_turns() if (t.get("learned") or "").strip()]
        return facts[-self.cfg.max_learned_facts :]

    # ------------------------------------------------------------------
    # Prompt assembly
    # ------------------------------------------------------------------
    def _state_block(self, answer: str, turn: int, extra: str = "") -> str:
        return render(
            self.prompts["turn_template"],
            answer=answer,
            turn=turn,
            max_turns=self.cfg.max_turns,
            elapsed=f"{self.minutes_elapsed():.1f}",
            duration=self.cfg.duration_minutes,
            topics="; ".join(self._topics()) or "none yet",
            learned=" | ".join(self._learned()) or "nothing yet",
            extra=f"- Note: {extra}" if extra else "",
        )

    def _build_messages(self, latest_user_content: str) -> list[dict]:
        messages = [{"role": "system", "content": self.system_prompt}]

        # Recent turns verbatim; older ones live on as "topics covered" / "learned".
        recent = self.transcript[-(self.cfg.history_turns * 2) :]
        for t in recent:
            if t["role"] == "assistant":
                messages.append({"role": "assistant", "content": self._assistant_json(t)})
            else:
                messages.append({"role": "user", "content": t["text"]})

        messages.append({"role": "user", "content": latest_user_content})
        return messages

    @staticmethod
    def _assistant_json(turn: dict) -> str:
        """Past interviewer turns are replayed in the same JSON format the model must produce."""
        return json.dumps(
            {
                "learned": turn.get("learned", ""),
                "topic": turn.get("topic", ""),
                "move": turn.get("move", ""),
                "end_interview": bool(turn.get("end", False)),
                "spoken_text": turn["text"],
            },
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------
    def _generate(self, user_content: str, *, allow_end: bool = True) -> dict:
        """
        Ask the model for the next interviewer turn (structured JSON, TURN_SCHEMA).

        Provider failures propagate as LLMError - there is deliberately no fallback
        text, so an outage can never be mistaken for something the interviewer said.
        """
        messages = self._build_messages(user_content)
        data: dict | None = None
        for _ in range(2):  # one extra try if the model returns an empty spoken line
            data = self.llm.chat_json(messages, TURN_SCHEMA)
            if str(data.get("spoken_text", "")).strip():
                break
            logger.warning("Interviewer returned an empty spoken_text; retrying once.")
            data = None
        if data is None:
            raise LLMResponseError("The model returned an empty interviewer line.")

        move = str(data.get("move", "")).strip()
        if move not in TURN_SCHEMA["properties"]["move"]["enum"]:
            move = "follow_up"   # bookkeeping label only; the spoken text is untouched
        data = {
            "learned": str(data.get("learned", "")).strip(),
            "topic": str(data.get("topic", "")).strip(),
            "move": move,
            "end_interview": bool(data.get("end_interview")) and allow_end,
            "spoken_text": str(data["spoken_text"]).strip(),
        }
        return data

    def _commit_ai_turn(self, data: dict) -> str:
        self.transcript.append(
            {
                "role": "assistant",
                "text": data["spoken_text"],
                "topic": data.get("topic", ""),
                "move": data.get("move", ""),
                "learned": data.get("learned", ""),
                "end": data["end_interview"],
                "ts": datetime.utcnow().isoformat(),
            }
        )
        if data["end_interview"]:
            self.finished = True
            self.end_reason = self.end_reason or "completed"
        return data["spoken_text"]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self) -> str:
        data = self._generate(self._start_prompt(), allow_end=False)
        return self._commit_ai_turn(data)

    def _start_prompt(self) -> str:
        return self.prompts["start_message"]

    def answer(self, candidate_text: str) -> str:
        """Register the candidate's answer and return the interviewer's next spoken line.

        Nothing is recorded unless generation succeeds, so a failed call can simply be retried.
        """
        text = candidate_text.strip()
        turn_no = self.turns + 1

        wrap_reason = self._wrapup_reason(turn_no)
        note = self.prompts["wrapup_note"] if wrap_reason else ""
        data = self._generate(self._state_block(text, turn_no, note))

        if wrap_reason:
            data["end_interview"] = True
            self.end_reason = wrap_reason
        elif data["end_interview"] and turn_no < self.cfg.min_turns_before_end:
            # Too early: ask again, telling the model not to end.
            data = self._generate(
                self._state_block(text, turn_no, self.prompts["min_turns_note"]),
                allow_end=False,
            )

        self.empty_streak = 0
        self.transcript.append(
            {"role": "candidate", "text": text, "ts": datetime.utcnow().isoformat()}
        )
        return self._commit_ai_turn(data)

    def register_empty(self, *, silence_timeout: bool = False) -> tuple[str | None, bool]:
        """
        The candidate produced no intelligible speech.

        Returns (spoken_text_or_None, finished). None means: just keep listening.
        """
        self.empty_streak += 1

        if self.empty_streak >= self.cfg.max_empty_streak:
            note, reason = self.prompts["unresponsive_note"], "unresponsive"
        elif silence_timeout or self.empty_streak >= self.cfg.empty_before_reprompt:
            note = self.prompts["checkin_note" if silence_timeout else "unclear_note"]
            reason = None
        else:
            return None, False

        data = self._generate(self._state_block("(no speech detected)", self.turns, note))
        if reason:
            data["end_interview"] = True
            self.end_reason = reason
        return self._commit_ai_turn(data), self.finished

    def _wrapup_reason(self, turn_no: int) -> str | None:
        if self.minutes_elapsed() >= self.cfg.duration_minutes:
            return "time_limit"
        if turn_no >= self.cfg.max_turns:
            return "max_turns"
        return None

    def end(self, reason: str = "candidate_ended") -> None:
        self.finished = True
        self.end_reason = reason

    def public_transcript(self) -> list[dict]:
        return list(self.transcript)
