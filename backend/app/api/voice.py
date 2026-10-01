"""Low-level speech utilities (handy for testing Whisper / Piper on their own)."""
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.services import voice_service

router = APIRouter(prefix="/voice", tags=["voice"])


class TTSRequest(BaseModel):
    text: str


@router.post("/stt")
def speech_to_text(audio: UploadFile = File(...)):
    data = audio.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio")
    return {"text": voice_service.transcribe(data)}


@router.post("/tts")
def text_to_speech(req: TTSRequest):
    return Response(content=voice_service.synthesize(req.text), media_type="audio/wav")
