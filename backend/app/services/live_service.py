"""
Real-time voice interview:  browser <-> this server <-> Gemini Live (Google GenAI SDK).

    browser mic (16 kHz PCM, binary frames) ----> Gemini Live
    browser speaker (24 kHz PCM, base64 JSON) <---- Gemini Live (+ transcripts)

The server sits in the middle on purpose: the API key / the VM's service account never
reach the browser, the resume-driven instructions are injected here, the transcript is
saved to the database turn by turn, and the interview's time/turn limits are enforced.

What this module guarantees (and the tests check):
  * the conversation continues across turns - the SDK's receive() stops after every model
    turn, so it is re-entered in a loop;
  * a failure is reported to the browser as an error, never replaced by invented speech;
  * Google's 15-minute audio session limit is bridged with session resumption;
  * the interviewer is told to wrap up before the time limit and is cut off at a hard stop;
  * a silent candidate is checked on, then the interview is closed (as in the turn-based flow).
"""
import asyncio
import base64
import json
import logging
import time
from datetime import datetime

from fastapi import WebSocket, WebSocketDisconnect
from google.genai import types
from sqlalchemy.orm import Session

from app.config import settings
from app.prompts.interviewer import render
from app.providers.gemini_provider import build_client, translate_error
from app.providers.llm_errors import LLMError
from app.services import session_manager
from app.services.interview_service import InterviewSession

logger = logging.getLogger(__name__)

TICK_SECONDS = 1.0   # how often the wrap-up / check-in timer looks at the clock

_FINISH_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="finish_interview",
            description=(
                "Call this ONLY after you have spoken your final thank-you and goodbye, "
                "to close the interview."
            ),
        )
    ]
)

_DEFAULT_LIVE_INSTRUCTIONS = (
    "REAL-TIME VOICE MODE. You are speaking, not writing: never output JSON, labels or stage directions, "
    "and ignore any earlier mention of JSON fields. Ask one short question at a time. Use only facts from the "
    "resume and from what the candidate says. Do not close the interview before $min_turns candidate answers. "
    "Aim for about $duration minutes and at most $max_turns candidate answers. When you are ready to close, thank "
    "the candidate, say the team will be in touch, ask no question, and then call finish_interview."
)


def build_live_instructions(session: InterviewSession) -> str:
    """Resume-driven system prompt (the JSON output format section removed) + voice-mode rules."""
    base = session.system_prompt.partition("OUTPUT FORMAT -")[0].rstrip()
    voice_rules = render(
        session.prompts.get("live_instructions", _DEFAULT_LIVE_INSTRUCTIONS),
        min_turns=session.cfg.min_turns_before_end,
        duration=session.cfg.duration_minutes,
        max_turns=session.cfg.max_turns,
    )
    return f"{base}\n\n{voice_rules}"


def build_live_config(session: InterviewSession, handle: str | None = None) -> types.LiveConnectConfig:
    fields: dict = {
        "response_modalities": ["AUDIO"],
        "system_instruction": build_live_instructions(session),
        "input_audio_transcription": types.AudioTranscriptionConfig(),
        "output_audio_transcription": types.AudioTranscriptionConfig(),
        "realtime_input_config": types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                silence_duration_ms=settings.LIVE_SILENCE_MS
            )
        ),
        "tools": [_FINISH_TOOL],
    }
    if settings.LIVE_SESSION_RESUMPTION:
        fields["session_resumption"] = types.SessionResumptionConfig(handle=handle)
    if settings.LIVE_VOICE:
        fields["speech_config"] = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.LIVE_VOICE)
            )
        )
    return types.LiveConnectConfig(**fields)


class LiveRelay:
    def __init__(self, websocket: WebSocket, db: Session, session_id: int, session: InterviewSession, client=None):
        self.ws = websocket
        self.db = db
        self.session_id = session_id
        self.session = session
        self.client = client
        self.prompts = session.prompts

        self.live = None                 # the currently open Gemini Live connection
        self.handle: str | None = None   # latest session-resumption handle
        self.candidate_buf: list[str] = []
        self.assistant_buf: list[str] = []
        self.finish_requested = False
        self.wrapup_sent = False
        self.model_speaking = False
        self.idle_since: float | None = None
        self.empty_streak = 0
        self.unresponsive_closing = False
        self.done = asyncio.Event()
        self.audio_chunks = 0
        self._watchdog: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    async def run(self) -> None:
        self.client = self.client or build_client()
        tasks = {
            asyncio.create_task(self._browser_loop()),
            asyncio.create_task(self._gemini_loop()),
            asyncio.create_task(self._timer_loop()),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            error = task.exception()
            if error and not isinstance(error, (WebSocketDisconnect, asyncio.CancelledError)):
                raise error

    # ------------------------------------------------------------------
    # Browser -> Gemini
    # ------------------------------------------------------------------
    async def _browser_loop(self) -> None:
        while True:
            packet = await self.ws.receive()
            if packet["type"] == "websocket.disconnect":
                return
            audio = packet.get("bytes")
            if audio:
                live = self.live
                if live is not None:
                    try:
                        await live.send_realtime_input(
                            audio=types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
                        )
                    except Exception as exc:  # connection is being replaced / closed: the Gemini loop handles it
                        logger.debug("Dropped an audio frame: %s", exc)
            elif packet.get("text"):
                try:
                    command = json.loads(packet["text"])
                except ValueError:
                    continue
                if isinstance(command, dict) and command.get("type") == "end":
                    self._finish("candidate_ended", "ended_early")
                    await self._send({"type": "finished"})
                    return

    # ------------------------------------------------------------------
    # Gemini -> browser (re-enters receive() every turn, reconnects when needed)
    # ------------------------------------------------------------------
    async def _gemini_loop(self) -> None:
        model = settings.live_model
        first_connection = True
        failures = 0
        while not self.done.is_set():
            try:
                config = build_live_config(self.session, self.handle)
                async with self.client.aio.live.connect(model=model, config=config) as live:
                    self.live = live
                    failures = 0
                    if first_connection:
                        first_connection = False
                        await self._send({"type": "ready", "model": model})
                        await live.send_realtime_input(text=self.prompts["start_message"])
                        self._watchdog = asyncio.create_task(self._no_audio_watchdog())
                    outcome = await self._pump(live)
                self.live = None
                if outcome == "finished":
                    return
                logger.info("Reconnecting the Gemini Live session (%s)", outcome)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.live = None
                if self.done.is_set():      # closing noise after a normal finish
                    return
                error = translate_error(exc)
                can_resume = bool(self.handle) and settings.LIVE_SESSION_RESUMPTION
                if can_resume and failures < settings.LIVE_MAX_RECONNECTS and not self.done.is_set():
                    failures += 1
                    logger.warning(
                        "Gemini Live connection lost (%s); resuming (%d/%d)",
                        error.message, failures, settings.LIVE_MAX_RECONNECTS,
                    )
                    await asyncio.sleep(min(2.0 * failures, 5.0))
                    continue
                raise error from exc

    async def _pump(self, live) -> str:
        """Read one connection until it is finished or has to be replaced."""
        while True:
            async for response in live.receive():     # receive() ends after every model turn
                outcome = await self._on_response(live, response)
                if outcome:
                    return outcome

    async def _on_response(self, live, response) -> str | None:
        update = getattr(response, "session_resumption_update", None)
        if update is not None and getattr(update, "new_handle", None) and getattr(update, "resumable", True) is not False:
            self.handle = update.new_handle

        if getattr(response, "go_away", None) is not None and self.handle and settings.LIVE_SESSION_RESUMPTION:
            return "go_away"      # Google is about to close this connection: continue on a new one

        tool_call = getattr(response, "tool_call", None)
        if tool_call:
            await self._on_tool_call(live, tool_call)

        content = getattr(response, "server_content", None)
        if content:
            await self._on_content(content)

        return "finished" if self.done.is_set() else None

    async def _on_tool_call(self, live, tool_call) -> None:
        replies = []
        for call in tool_call.function_calls or []:
            result: dict = {"ok": True}
            if call.name == "finish_interview":
                too_early = self.session.turns < self.session.cfg.min_turns_before_end and not self._time_is_up()
                if too_early:
                    result = {"ok": False, "error": self.prompts.get(
                        "live_too_early_note", "It is too early to finish. Continue with another relevant question.")}
                else:
                    self.finish_requested = True
            replies.append(types.FunctionResponse(id=call.id, name=call.name, response=result))
        if replies:
            await live.send_tool_response(function_responses=replies)

    async def _on_content(self, content) -> None:
        if getattr(content, "interrupted", False):
            self.model_speaking = False
            await self._send({"type": "interrupted"})

        heard = getattr(content, "input_transcription", None)
        if heard is not None and getattr(heard, "text", None):
            self.candidate_buf.append(heard.text)
            self.idle_since = None
            self.empty_streak = 0

        spoken = getattr(content, "output_transcription", None)
        if spoken is not None and getattr(spoken, "text", None):
            await self._model_started()
            self.assistant_buf.append(spoken.text)
            await self._send({"type": "transcription", "role": "assistant", "text": spoken.text})

        model_turn = getattr(content, "model_turn", None)
        if model_turn is not None:
            for part in getattr(model_turn, "parts", None) or []:
                data = getattr(getattr(part, "inline_data", None), "data", None)
                if data:
                    self.audio_chunks += 1
                    if self.audio_chunks == 1:
                        logger.info("Live session %s: first audio chunk from Gemini (%d bytes)", self.session_id, len(data))
                    await self._model_started()
                    await self._send({"type": "audio", "data": base64.b64encode(data).decode("ascii")})

        if getattr(content, "turn_complete", False):
            await self._on_turn_complete()

    async def _no_audio_watchdog(self) -> None:
        """Tell the browser if Gemini never produces audio after the greeting trigger."""
        await asyncio.sleep(25)
        if self.audio_chunks == 0 and not self.done.is_set():
            logger.error("Live session %s: no audio from %s after 25s", self.session_id, settings.live_model)
            await self._send({
                "type": "error",
                "message": "AI interviewer unavailable: the Live model returned no audio. "
                           "Check GEMINI_LIVE_MODEL and that your API key has Live API access.",
            })

    async def _model_started(self) -> None:
        if not self.model_speaking:
            self.model_speaking = True
            self.idle_since = None

    async def _flush_candidate(self) -> None:
        text = "".join(self.candidate_buf).strip()
        self.candidate_buf.clear()
        if text:
            self.session.transcript.append(
                {"role": "candidate", "text": text, "ts": datetime.utcnow().isoformat()}
            )
            await self._send({"type": "transcription", "role": "candidate", "text": text})

    async def _on_turn_complete(self) -> None:
        # Google can deliver the candidate's transcript in pieces up to the end of the turn, so it is
        # assembled here - and always recorded BEFORE the interviewer's reply to it.
        await self._flush_candidate()
        text = "".join(self.assistant_buf).strip()
        self.assistant_buf.clear()
        if text:
            self.session.transcript.append(
                {
                    "role": "assistant", "text": text, "topic": "", "move": "live", "learned": "",
                    "end": self.finish_requested, "ts": datetime.utcnow().isoformat(),
                }
            )
        self.model_speaking = False
        self.idle_since = time.monotonic()

        if self.finish_requested:
            return await self._complete("completed", "finished")
        if self.unresponsive_closing:
            return await self._complete("unresponsive", "ended_early")
        if self.session.turns >= self.session.cfg.max_turns:
            if self.wrapup_sent:
                return await self._complete("max_turns", "finished")
            await self._send_note(self.prompts.get("live_wrapup_note", self.prompts["wrapup_note"]))
            self.wrapup_sent = True

        session_manager.persist(self.db, self.session_id, self.session)
        await self._send({"type": "turn_complete"})

    # ------------------------------------------------------------------
    # Time limits and silent candidates
    # ------------------------------------------------------------------
    async def _timer_loop(self) -> None:
        cfg = self.session.cfg
        while not self.done.is_set():
            await asyncio.sleep(TICK_SECONDS)
            if self.live is None:
                continue
            elapsed = self.session.minutes_elapsed() * 60
            limit = cfg.duration_minutes * 60

            if not self.wrapup_sent and elapsed >= limit - settings.LIVE_WRAPUP_LEAD_SECONDS:
                self.wrapup_sent = True
                await self._send_note(self.prompts.get("live_wrapup_note", self.prompts["wrapup_note"]))
            if elapsed >= limit + settings.LIVE_FORCE_END_GRACE_SECONDS:
                await self._flush_candidate()
                await self._complete("time_limit", "finished")
                return

            timeout = settings.LIVE_SILENCE_CHECKIN_SECONDS
            if (
                timeout and self.idle_since is not None and not self.model_speaking
                and time.monotonic() - self.idle_since >= timeout
            ):
                self.idle_since = time.monotonic()
                self.empty_streak += 1
                if self.empty_streak >= cfg.max_empty_streak:
                    self.unresponsive_closing = True
                    await self._send_note(self.prompts.get("live_unresponsive_note", self.prompts["unresponsive_note"]))
                else:
                    await self._send_note(self.prompts.get("live_checkin_note", self.prompts["checkin_note"]))

    def _time_is_up(self) -> bool:
        return self.session.minutes_elapsed() >= self.session.cfg.duration_minutes

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _send_note(self, text: str) -> None:
        live = self.live
        if live is not None:
            try:
                await live.send_realtime_input(text=f"[Note to the interviewer - not from the candidate] {text}")
            except Exception as exc:
                logger.warning("Could not send a note to the interviewer: %s", exc)

    async def _complete(self, reason: str, close: str) -> None:
        self._finish(reason, close)
        await self._send({"type": "finished"})

    def _finish(self, reason: str, close: str) -> None:
        if self.done.is_set():
            return
        self.session.end(reason)
        session_manager.persist(self.db, self.session_id, self.session, close=close)
        self.done.set()

    async def _send(self, payload: dict) -> None:
        try:
            await self.ws.send_json(payload)
        except Exception as exc:   # browser already gone; the browser loop ends the session
            logger.debug("Browser gone, dropped %s: %s", payload.get("type"), exc)


async def run_live_interview(websocket: WebSocket, db: Session, session_id: int, session: InterviewSession, client=None) -> None:
    """Run one live interview. Errors are reported to the browser, then re-raised for logging."""
    relay = LiveRelay(websocket, db, session_id, session, client=client)
    try:
        await relay.run()
    except WebSocketDisconnect:
        pass
    except LLMError as exc:
        logger.error("Live interview failed: %s | %s", exc.message, exc.detail)
        await relay._send({"type": "error", "message": f"AI interviewer unavailable: {exc.message}"})
    except Exception:
        logger.exception("Live interview failed unexpectedly")
        await relay._send({"type": "error", "message": "The live interview stopped unexpectedly. Please try again."})