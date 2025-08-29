import asyncio
import websockets
import sounddevice as sd
import numpy as np
import miniaudio # MP3 디코딩 및 재생을 위한 라이브러리
import queue     # 비동기-동기 브릿지를 위한 큐

# --- 설정 ---
SERVER_URL = "ws://localhost:8000/ws/s2s"
# 마이크 입력 설정
INPUT_SAMPLE_RATE = 16000
INPUT_CHANNELS = 1
INPUT_DTYPE = "float32"
INPUT_BLOCKSIZE = 1024
# 스피커 출력 설정 (OpenAI TTS 기본값)
OUTPUT_SAMPLE_RATE = 24000
OUTPUT_CHANNELS = 1
OUTPUT_FORMAT = miniaudio.SampleFormat.SIGNED16


async def main():
    """
    서버에 연결하여 마이크 입력을 보내고,
    실시간으로 음성 응답을 받아 디코딩 후 재생합니다.
    """
    # miniaudio 콜백과 asyncio를 연결하기 위한 동기 큐
    sync_audio_queue = queue.Queue()
    
    # 스피커 출력 장치 초기화
    playback_device = miniaudio.PlaybackDevice(
        output_format=OUTPUT_FORMAT,
        nchannels=OUTPUT_CHANNELS,
        sample_rate=OUTPUT_SAMPLE_RATE
    )
    
    try:
        async with websockets.connect(SERVER_URL) as websocket:
            print("✅ 서버에 연결되었습니다.")

            # --- Task 1: 마이크 입력을 서버로 전송 ---
            async def recorder():
                loop = asyncio.get_event_loop()
                input_queue = asyncio.Queue()

                def callback(indata, frames, time, status):
                    # 오디오 스레드에서 메인 이벤트 루프로 데이터를 안전하게 전달
                    loop.call_soon_threadsafe(input_queue.put_nowait, indata.copy())

                print("🎙️ 마이크 입력을 시작합니다... (말씀하시면 됩니다)")
                with sd.InputStream(
                    samplerate=INPUT_SAMPLE_RATE,
                    channels=INPUT_CHANNELS,
                    dtype=INPUT_DTYPE,
                    blocksize=INPUT_BLOCKSIZE,
                    callback=callback
                ):
                    # ConnectionClosed 예외로 루프를 종료하므로 while True로 변경
                    while True:
                        try:
                            indata = await input_queue.get()
                            # Deepgram이 요구하는 16-bit PCM 형식으로 변환하여 전송
                            pcm16 = (indata * 32767).astype(np.int16).tobytes()
                            await websocket.send(pcm16)
                        except websockets.ConnectionClosed:
                            break
                        except Exception as e:
                            print(f"녹음 중 오류: {e}")
                            break
                print("녹음 스트림이 종료되었습니다.")

            # --- Task 2: 서버로부터 MP3 청크를 받아 동기 큐에 저장 ---
            async def receiver():
                print("🔊 서버로부터 AI 음성 응답을 기다립니다...")
                try:
                    async for message in websocket:
                        # 비동기(websockets) -> 동기(queue)로 데이터 전달
                        sync_audio_queue.put(message)
                except websockets.ConnectionClosed:
                    pass
                except Exception as e:
                    print(f"음성 수신 중 오류: {e}")
                finally:
                    # 스트림 종료 신호 전송
                    sync_audio_queue.put(None)
                print("수신 스트림이 종료되었습니다.")

            # --- miniaudio를 위한 안정적인 동기 오디오 제너레이터 (StreamDecoder 대체) ---
            def audio_playback_generator():
                pcm_buffer = bytearray()
                mp3_buffer = bytearray() # MP3 청크를 모으기 위한 버퍼
                
                # 제너레이터를 .send() 호출에 대비시킴 (Priming)
                framecount = yield b''

                while True:
                    required_bytes = framecount * playback_device.sample_width * playback_device.nchannels
                    
                    while len(pcm_buffer) < required_bytes:
                        try:
                            mp3_chunk = sync_audio_queue.get(block=False)
                            if mp3_chunk is None:
                                # 스트림 종료 신호, 남은 버퍼 처리
                                if mp3_buffer:
                                    try:
                                        # 수정: 디코딩 시 출력 형식 명시
                                        decoded = miniaudio.decode(bytes(mp3_buffer),
                                                                   output_format=OUTPUT_FORMAT,
                                                                   nchannels=OUTPUT_CHANNELS,
                                                                   sample_rate=OUTPUT_SAMPLE_RATE)
                                        pcm_buffer.extend(decoded.samples)
                                    except miniaudio.DecodeError:
                                        print("경고: 스트림 마지막의 MP3 버퍼를 디코딩할 수 없습니다.")
                                if pcm_buffer:
                                    yield bytes(pcm_buffer) # 남은 오디오 재생
                                return

                            mp3_buffer.extend(mp3_chunk)
                            
                            # 버퍼의 내용을 디코딩 시도
                            try:
                                # 수정: 디코딩 시 출력 형식 명시
                                decoded = miniaudio.decode(bytes(mp3_buffer),
                                                           output_format=OUTPUT_FORMAT,
                                                           nchannels=OUTPUT_CHANNELS,
                                                           sample_rate=OUTPUT_SAMPLE_RATE)
                                # 성공하면 PCM 데이터를 pcm_buffer에 추가하고 mp3_buffer를 비움
                                pcm_buffer.extend(decoded.samples)
                                mp3_buffer.clear()
                            except miniaudio.DecodeError:
                                # 디코딩 실패. 데이터가 불완전할 수 있으므로 다음 청크를 기다림.
                                pass

                        except queue.Empty:
                            # 큐가 비어있으면 루프를 빠져나가 조용한 오디오를 재생합니다.
                            break
                    
                    if len(pcm_buffer) >= required_bytes:
                        output_chunk = pcm_buffer[:required_bytes]
                        del pcm_buffer[:required_bytes]
                        framecount = yield bytes(output_chunk)
                    else:
                        # 데이터가 부족하면 조용한 오디오를 재생하여 끊김 방지
                        silence = bytearray(required_bytes)
                        framecount = yield silence
            
            # 태스크 실행
            recorder_task = asyncio.create_task(recorder())
            receiver_task = asyncio.create_task(receiver())
            
            # 오디오 재생 시작
            playback_generator = audio_playback_generator()
            # 제너레이터를 시작하기 위해 next() 호출 (Priming)
            next(playback_generator)
            playback_device.start(playback_generator)

            # 두 태스크가 모두 완료될 때까지 대기
            await asyncio.gather(recorder_task, receiver_task)

    except Exception as e:
        print(f"클라이언트 실행 중 오류 발생: {e}")
    finally:
        playback_device.close()
        print("클라이언트 세션을 종료합니다.")


if __name__ == "__main__":
    try:
        # 필요한 라이브러리가 설치되어 있는지 확인
        import sounddevice
        import numpy
        import miniaudio
    except ImportError as e:
        print("필요한 라이브러리가 설치되지 않았습니다. 아래 명령어를 실행해주세요:")
        print(f"pip install sounddevice numpy miniaudio")
        exit(1)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n프로그램을 종료합니다.")

