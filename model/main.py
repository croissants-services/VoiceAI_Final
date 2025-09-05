import asyncio
import os
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Literal, Union
from enum import Enum
import sys

root = os.path.join(os.path.dirname(os.getcwd()))
sys.path.append(root)

# 우리가 만든 모듈들을 가져옵니다.
from .stt import STTModel
from .llm import LLMModel
from .tts import TTSModel

# --- add to your FastAPI websocket handler file (e.g., main.py) ---
import base64, struct, numpy as np
import json
from datetime import datetime

def _audio_stats_from_pcm16le(pcm_bytes: bytes):
    if not pcm_bytes:
        return {"n": 0, "rms": 0.0, "peak": 0}
    if len(pcm_bytes) % 2 != 0:
        return {"n": 0, "rms": 0.0, "peak": 0, "error": "bytes not even (not int16-aligned)"}
    n = len(pcm_bytes) // 2
    samples = struct.unpack("<" + "h"*n, pcm_bytes[:n*2])
    arr = np.frombuffer(np.asarray(samples, dtype=np.int16), dtype=np.int16).astype(np.float32)
    rms = float(np.sqrt(np.mean(arr**2)))
    peak = int(np.max(np.abs(arr)))
    return {"n": n, "rms": rms, "peak": peak}

def _maybe_decode_payload(payload: bytes | str):
    """
    Decodes incoming payload into raw PCM bytes when possible.
    Priority:
      1) bytes/bytearray → assume raw PCM ("binary")
      2) JSON string with base64 under common keys ("json(base64)")
      3) bare base64 string ("base64")
      4) fallback to UTF-8 bytes of the text ("text")
    """
    # 1) Direct binary
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload), "binary"

    # 2) JSON string with base64 field
    if isinstance(payload, str):
        # Try to parse JSON and extract a base64 field if present
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                # typical keys seen in VoiceAI: chunk_data, data, audio_data
                cand_keys = [
                    "chunk_data", "data", "audio_data", "audio", "b64", "base64"
                ]
                for k in cand_keys:
                    v = obj.get(k)
                    if isinstance(v, str):
                        try:
                            decoded = base64.b64decode(v, validate=True)
                            return decoded, "json(base64)"
                        except Exception:
                            # keep searching other keys
                            pass
        except Exception:
            # not JSON or not a dict → fall through to direct base64
            pass

        # 3) Bare base64 string
        try:
            decoded = base64.b64decode(payload, validate=True)
            return decoded, "base64"
        except Exception:
            # 4) Fallback to raw UTF-8 bytes (for logging/diagnostics)
            return payload.encode("utf-8", errors="ignore"), "text"

    # Unknown type
    return b"", "unknown"

async def _log_audio_chunk(prefix: str, raw: bytes | str):
    data, dtype = _maybe_decode_payload(raw)
    # raw PCM으로 가정하고 통계 찍기
    stats = _audio_stats_from_pcm16le(data)
    now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[WS][{now}] {prefix} type={dtype} bytes={len(data)} stats={stats}")
    # 포맷 힌트
    if dtype == "text":
        print("[WARN] text frame 수신 — base64 or JSON 래핑 여부 확인 필요")
    if len(data) and len(data) % 2 != 0:
        print("[WARN] int16 정렬 오류(바이트 수가 2의 배수 아님) — 인코딩 또는 전송 경로 점검 필요")

# --- STT 진단 로그 헬퍼들 ---
def _log_stt_config(stt_obj):
    enc = getattr(stt_obj, "encoding", None) or getattr(stt_obj, "ENCODING", None) or "(unknown)"
    sr = getattr(stt_obj, "sample_rate", None) or getattr(stt_obj, "SAMPLE_RATE", None) or "(unknown)"
    ch = getattr(stt_obj, "channels", None) or getattr(stt_obj, "CHANNELS", None) or "(unknown)"
    lang = getattr(stt_obj, "language", None) or getattr(stt_obj, "LANGUAGE", None) or "(unknown)"
    print(f"[STT] config: encoding={enc} sample_rate={sr} channels={ch} language={lang}")

async def _maybe_set_stt_logging_hooks(stt_obj):
    """STTModel이 훅 등록을 지원하는 경우(선택적), 전송 바이트/결과 로그를 찍도록 설정합니다."""
    try:
        setter = getattr(stt_obj, "set_logging_hooks", None)
        if callable(setter):
            def on_send_frame(frame_bytes: bytes):
                print(f"[STT] send frame bytes={len(frame_bytes)}")
            def on_result(is_final: bool, duration_s: float, text: str | None, confidence: float | None = None):
                # duration과 confidence가 없으면 '(n/a)'로 출력
                d = f"{duration_s:.2f}s" if isinstance(duration_s, (int, float)) else "n/a"
                c = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "n/a"
                t = (text or "").strip()
                print(f"[STT] got result: is_final={is_final} dur={d} text=\"{t}\" conf={c}")
            setter(on_send_frame=on_send_frame, on_result=on_result)
    except Exception as e:
        print(f"[STT] hook setup skipped: {e}")

# .env 파일에서 환경 변수 로드
load_dotenv(dotenv_path=".env")

# --- 0. pydantic 모델 정의 ---
class ChatRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"

class ChatMessage(BaseModel):
    id: str
    role: ChatRole
    text: str

class SttInterimResultMessage(BaseModel):
    type: Literal['stt_interim_result']
    transcript: str

class VoiceInput(BaseModel):
    type: Literal['voice']
    data: str  # 또는 bytes, 필요에 따라
    
class VoiceChunk(BaseModel):
    type: Literal['voicechunk']
    chunk_data: str  # 또는 bytes
    sequence: int = 0
    is_final: bool = False

class TTSAudioChunk(BaseModel):
    type: Literal['tts_audio']
    audio_data: bytes
    sequence: int = 0
    is_final: bool = False

class ErrorMessage(BaseModel):
    type: Literal['error']
    message: str
    error_code: str = "UNKNOWN_ERROR"

# 메시지 타입 유니온
MessageType = Union[
    ChatMessage, 
    SttInterimResultMessage, 
    VoiceInput, 
    VoiceChunk,
    TTSAudioChunk,
    ErrorMessage
]

# --- 1. 모든 AI 모델을 관리하는 서비스 클래스 ---
class SpeechService:
    def __init__(self):
        print("SpeechService 초기화 시작...")
        self.stt_model = STTModel()
        self.llm_model = LLMModel()
        self.tts_model = TTSModel()
        print("SpeechService 초기화 완료.")

# --- 2. FastAPI 앱 및 서비스 객체 생성 ---
app = FastAPI()

@app.on_event("startup")
async def _on_startup():
    print("[FastAPI] startup complete — app is ready")

speech_service = SpeechService()

@app.get("/healthz")
async def healthz():
    return {"ok": True}

@app.get("/diag")
async def diag():
    stt = speech_service.stt_model
    enc = getattr(stt, "encoding", None) or "(unknown)"
    sr = getattr(stt, "sample_rate", None) or "(unknown)"
    ch = getattr(stt, "channels", None) or "(unknown)"
    lang = getattr(stt, "language", None) or "(unknown)"
    return {
        "ok": True,
        "stt": {"encoding": enc, "sample_rate": sr, "channels": ch, "language": lang}
    }

# --- 3. WebSocket 연결 상태 확인 헬퍼 함수 ---
def is_websocket_connected(websocket: WebSocket) -> bool:
    try:
        # WebSocket 상태 확인 방법들
        if hasattr(websocket, 'client_state'):
            from fastapi.websockets import WebSocketState
            return websocket.client_state == WebSocketState.CONNECTED
        elif hasattr(websocket, '_state'):
            return websocket._state == 1  # CONNECTED state
        else:
            # fallback: client 객체의 closed 속성 확인
            return hasattr(websocket.client, 'closed') and not websocket.client.closed
    except Exception:
        return False

# --- 4. WebSocket 엔드포인트 정의 ---
@app.websocket("/ws/s2s")
async def websocket_endpoint(websocket: WebSocket):
    client_host = websocket.client.host
    client_port = websocket.client.port
    print(f"[Model] WebSocket connection attempt from {client_host}:{client_port}")
    await websocket.accept()
    print(f"[Model] WebSocket connection accepted for {client_host}:{client_port}")
    
    # 연결 상태 추적
    is_connected = True
    
    try:
        # --- 연속적인 대화를 위한 while 루프 ---
        while is_connected:

            try:
                # --- STT 단계 ---
                print(f"[Model] Handing WebSocket directly to STT (no pre-read) for {client_host}:{client_port}...")
                _log_stt_config(speech_service.stt_model)
                await _maybe_set_stt_logging_hooks(speech_service.stt_model)
                full_transcript = await speech_service.stt_model.transcribe_stream(websocket)
                print(f'[Model] Received full transcript: {full_transcript}')
                if not full_transcript:
                    print("[STT] empty final transcript — waiting for next turn")
                    await asyncio.sleep(0.05)
                    continue
                print(f"[STT] summary: final_text=\"{(full_transcript or '').strip()}\"")
                # 연결 상태 확인
                if not is_websocket_connected(websocket):
                    print("🔌 WebSocket 연결 상태 변경 감지")
                    break
                
                if full_transcript:
                    print(f"🎤 최종 STT 결과: {full_transcript}")

                    # --- LLM + TTS 파이프라인 단계 ---
                    text_generator = speech_service.llm_model.generate_text_stream(full_transcript)
                    audio_chunk_generator = speech_service.tts_model.synthesize_audio_stream(text_generator)

                    # 생성되는 음성 조각을 즉시 클라이언트로 전송합니다.
                    async for audio_chunk in audio_chunk_generator:
                        # 전송 전 연결 상태 재확인
                        if not is_websocket_connected(websocket):
                            print("🔌 전송 중 연결 끊어짐 감지")
                            is_connected = False
                            break
                        await websocket.send_bytes(audio_chunk)
                    
                    print("🔊 음성 응답 전송 완료")
                
                # 짧은 대기로 CPU 사용률 최적화
                await asyncio.sleep(0.1)
                
            except WebSocketDisconnect:
                print("🔌 클라이언트 연결 해제 (내부)")
                is_connected = False
                break
            except asyncio.CancelledError:
                print("🔌 작업 취소됨")
                is_connected = False
                break
            except Exception as e:
                print(f"🚫 내부 루프 오류: {e}")
                # 일부 오류는 계속 진행 가능
                if "disconnect" in str(e).lower() or "close" in str(e).lower():
                    is_connected = False
                    break
                # 다른 오류는 로그만 남기고 계속 진행
                continue

    except WebSocketDisconnect:
        print(f"[Model] WebSocket disconnected (external) for {client_host}:{client_port}")
    except Exception as e:
        print(f"[Model] An overall exception occurred for {client_host}:{client_port}: {e}")
    finally:
        # 정리 작업
        try:
            # STT 모델의 연결 정리 (Deepgram 등)
            if hasattr(speech_service.stt_model, 'cleanup'):
                await speech_service.stt_model.cleanup()
        except Exception as cleanup_error:
            print(f"[Model] Error during cleanup for {client_host}:{client_port}: {cleanup_error}")
        

        print(f"[Model] Client session ended for {client_host}:{client_port}")
