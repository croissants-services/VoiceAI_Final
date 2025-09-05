# stt.py
import asyncio
import websockets
import json
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

class STTModel:
    def __init__(self):
        """
        Deepgram API 설정을 초기화합니다.
        """
        self.api_key = os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPGRAM_API_KEY가 설정되지 않았습니다.")
            
        self.dg_url = (
            "wss://api.deepgram.com/v1/listen"
            "?model=nova-2&language=ko&punctuate=true"
            "&interim_results=false"
            "&encoding=linear16&sample_rate=16000&channels=1"
            "&multichannel=false&smart_format=true"
        )
        print("STTModel (Deepgram) 초기화 완료.")

    async def transcribe_stream(self, client_websocket):
        """
        클라이언트 웹소켓으로부터 오디오를 받아 Deepgram으로 보내고, 최종 텍스트를 반환합니다.
        """
        async with websockets.connect(
            self.dg_url,
            additional_headers={"Authorization": f"Token {self.api_key}"}
        ) as dg_websocket:
                        
            async def forward_to_deepgram():
                """클라이언트의 오디오를 Deepgram으로 전달"""
                print(f"[STT] Forwarding audio to Deepgram process started.")
                bytes_total = 0
                last_log = asyncio.get_event_loop().time()
                try:
                    while True:
                        message = await client_websocket.receive()
                        if audio_data := message.get("bytes"):
                            await dg_websocket.send(audio_data)
                            bytes_total += len(audio_data)
                        elif (t := message.get("text")) is not None:
                            # 제어 메시지는 무시/필요 시 처리
                            try:
                                j = json.loads(t)
                                print(f"[STT] control/text from client: {j}")
                            except Exception:
                                pass
                        # 주기적 전송량 로그
                        now = asyncio.get_event_loop().time()
                        if now - last_log >= 1.0:
                            print(f"[STT] forwarded {bytes_total} bytes to Deepgram (running)")
                            last_log = now
                except websockets.ConnectionClosed:
                    print("[STT] 클라이언트 연결이 끊겼습니다.")
                except Exception as e:
                    print(f"[STT ERROR forwarder]: {e}")

            async def receive_from_deepgram():
                """Deepgram으로부터 결과를 받아 최종 텍스트를 찾음"""
                final_transcript = ""
                print(f"[STT] Receiving results from Deepgram process started.")
                try:
                    async for msg in dg_websocket:
                        print(f"[STT] Raw message from Deepgram: {msg}")
                        data = json.loads(msg)
                        print(f"[STT] Parsed data: {data}")

                        ch = data.get("channel", {})
                        alt = (ch.get("alternatives") or [{}])[0]
                        transcript = alt.get("transcript", "") or ""
                        is_final = bool(data.get("is_final"))
                        speech_final = bool(data.get("speech_final"))

                        if transcript:
                            final_transcript = transcript
                            print(f"Deepgram으로부터 최종 텍스트 수신: {final_transcript}")
                            break
                        # 최종 플래그가 섰는데 비어 있으면 무음일 가능성 → 계속 루프 돌지 않도록 탈출
                        if is_final and speech_final and not transcript:
                            print("[STT] final but empty transcript (likely silence) — breaking")
                            break
                except websockets.ConnectionClosed:
                     print("[STT ERROR] Deepgram 연결이 끊겼습니다 (receiver).")
                except Exception as e:
                    print(f"결과 수신 중 오류 (receiver): {e}")
                return final_transcript

            async def send_keepalive():
                """Deepgram 연결 유지를 위해 10초마다 KeepAlive 메시지를 전송합니다."""
                try:
                    while True:
                        await dg_websocket.send(json.dumps({"type": "KeepAlive"}))
                        await asyncio.sleep(10)
                except (asyncio.CancelledError, websockets.ConnectionClosed):
                    pass # 태스크가 취소되거나 연결이 끊기면 정상 종료

            # 세 개의 작업을 동시에 실행: 오디오 전송, 결과 수신, 연결 유지
            forwarder_task = asyncio.create_task(forward_to_deepgram())
            receiver_task = asyncio.create_task(receive_from_deepgram())
            keepalive_task = asyncio.create_task(send_keepalive())
            
            tasks = [forwarder_task, receiver_task, keepalive_task]

            # 작업 중 하나라도 먼저 끝나면, 나머지 작업을 정리하고 결과를 반환
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED
            )

            for task in pending:
                task.cancel() # 남은 태스크 취소

            # receiver_task가 결과를 가졌는지 확인
            if receiver_task in done and not receiver_task.cancelled():
                return receiver_task.result()
            
            return None # 최종 텍스트를 받지 못한 경우
