import asyncio
import os
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Literal, Union
from enum import Enum

# 우리가 만든 모듈들을 가져옵니다.
from .stt import STTModel
from .llm import LLMModel
from .tts import TTSModel

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
speech_service = SpeechService()

# --- 3. WebSocket 엔드포인트 정의 ---
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
                print(f"[Model] Waiting for audio stream from {client_host}:{client_port}...")
                full_transcript = await speech_service.stt_model.transcribe_stream(websocket)
                
                # 연결 상태 확인
                if websocket.application_state != WebSocket.CONNECTED:
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
                        if websocket.application_state != WebSocket.CONNECTED:
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
