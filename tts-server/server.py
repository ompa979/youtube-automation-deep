import os
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI(title="ExamCracker Chatterbox TTS")

TOKEN = os.getenv("TTS_SERVER_TOKEN", "")

class TTSRequest(BaseModel):
    text: str
    voice: str | None = None
    exaggeration: float = 0.5
    cfg_weight: float = 0.5

@app.get("/health")
def health():
    return {"status": "ok"}

def check_token(authorization: str | None):
    if TOKEN and authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/tts")
def tts(req: TTSRequest, authorization: str | None = Header(default=None)):
    check_token(authorization)

    # Lazy import so the health endpoint works even while the model is loading.
    try:
        from chatterbox.tts import ChatterboxTTS
        import torch
        import io
        import torchaudio
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS dependencies unavailable: {e}")

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    try:
        model = ChatterboxTTS.from_pretrained(device="cuda" if torch.cuda.is_available() else "cpu")
        wav = model.generate(
            req.text,
            exaggeration=req.exaggeration,
            cfg_weight=req.cfg_weight,
        )
        buf = io.BytesIO()
        torchaudio.save(buf, wav, model.sr, format="wav")
        return Response(content=buf.getvalue(), media_type="audio/wav")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {e}")
