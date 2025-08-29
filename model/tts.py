import asyncio
import openai
import os
import time # 시간 측정을 위해 time 모듈 추가

class TTSModel:
    def __init__(self):
        """
        OpenAI TTS 클라이언트를 초기화합니다.
        """
        self.client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        print("TTSModel (OpenAI) 초기화 완료.")

    async def synthesize_audio_stream(self, text_generator):
        """
        LLM이 생성한 텍스트 제너레이터(문장 스트림)를 입력으로 받아,
        음성 데이터 청크를 스트리밍하는 비동기 제너레이터를 반환합니다.
        """
        print("TTS 음성 합성 스트리밍 시작...")
        
        total_start_time = time.perf_counter() # 전체 TTS 프로세스 시작 시간

        # LLM이 문장을 생성할 때마다 반복
        async for sentence in text_generator:
            if not sentence.strip(): # 공백만 있는 문장은 건너뛰기
                continue
            
            print(f"TTS 입력 문장: {sentence}")
            
            try:
                sentence_start_time = time.perf_counter() # 개별 문장 처리 시작 시간
                first_byte_received = False

                # OpenAI의 스트리밍 TTS API 호출
                async with self.client.audio.speech.with_streaming_response.create(
                    model="gpt-4o-mini-tts",
                    voice="nova",
                    input=sentence,
                    response_format="mp3"
                ) as response:
                    # TTS API가 반환하는 음성 데이터 청크를 그대로 클라이언트로 yield
                    async for audio_chunk in response.iter_bytes():
                        # 첫 번째 바이트 수신 시간 측정 (TTFB)
                        if not first_byte_received:
                            ttfb = time.perf_counter() - sentence_start_time
                            print(f"   ⏱️ [TTS] 첫 바이트 수신 시간 (TTFB): {ttfb:.4f}초")
                            first_byte_received = True
                        
                        yield audio_chunk

                sentence_end_time = time.perf_counter()
                print(f"   ⏱️ [TTS] 문장 음성 생성 완료 시간: {sentence_end_time - sentence_start_time:.4f}초")

            except Exception as e:
                print(f"TTS API 호출 중 오류 발생: {e}")
        
        total_end_time = time.perf_counter()
        print(f"\n⏱️ [TTS] 전체 음성 합성 프로세스 완료. 총 소요 시간: {total_end_time - total_start_time:.4f}초")
