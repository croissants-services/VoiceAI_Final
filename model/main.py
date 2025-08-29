import asyncio
import os
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# 우리가 만든 모듈들을 가져옵니다.
from stt import STTModel
from llm import LLMModel
from tts import TTSModel

# .env 파일에서 환경 변수 로드
load_dotenv(dotenv_path=".env")

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
async def websocket_s2s_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🔗 클라이언트 연결 성공")
    try:
        # --- 연속적인 대화를 위한 while 루프 추가 ---
        while True:
            # --- STT 단계 ---
            full_transcript = await speech_service.stt_model.transcribe_stream(websocket)
            
            if full_transcript:
                print(f"🎤 최종 STT 결과: {full_transcript}")

                # --- LLM + TTS 파이프라인 단계 ---
                text_generator = speech_service.llm_model.generate_text_stream(full_transcript)
                audio_chunk_generator = speech_service.tts_model.synthesize_audio_stream(text_generator)

                # 생성되는 음성 조각을 즉시 클라이언트로 전송합니다.
                async for audio_chunk in audio_chunk_generator:
                    await websocket.send_bytes(audio_chunk)
                
                print("🔊 음성 응답 전송 완료")
            else:
                # STT 결과가 없으면 대기 (클라이언트가 조용할 경우 등)
                print("... 대기 중 ...")

    except WebSocketDisconnect:
        print("🔌 클라이언트 연결 해제")
    except Exception as e:
        print(f"🚫 오류 발생: {e}")
    finally:
        print("클라이언트 세션 종료")

