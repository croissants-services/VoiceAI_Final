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
            "&interim_results=false&encoding=linear16" # interim_results는 false로 설정
            "&sample_rate=16000&channels=1"
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
            
            print("Deepgram 서버에 연결되었습니다. 이제 마이크에 말씀하세요.")
            
            async def forward_to_deepgram():
                """클라이언트의 오디오를 Deepgram으로 전달"""
                try:
                    while True:
                        audio_data = await client_websocket.receive_bytes()
                        await dg_websocket.send(audio_data)
                except websockets.ConnectionClosed:
                    print("클라이언트 연결이 끊겼습니다 (forwarder).")
                except Exception as e:
                    print(f"오디오 전달 중 오류 (forwarder): {e}")

            async def receive_from_deepgram():
                """Deepgram으로부터 결과를 받아 최종 텍스트를 찾음"""
                final_transcript = ""
                try:
                    async for msg in dg_websocket:
                        data = json.loads(msg)
                        transcript = data.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                        if transcript:
                            final_transcript = transcript
                            print(f"Deepgram으로부터 최종 텍스트 수신: {final_transcript}")
                            break # 최종 결과가 나오면 루프 종료
                except websockets.ConnectionClosed:
                     print("Deepgram 연결이 끊겼습니다 (receiver).")
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

