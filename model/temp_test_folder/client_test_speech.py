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
#INPUT_BLOCKSIZE = 1024
INPUT_BLOCKSIZE = int(INPUT_SAMPLE_RATE * 0.02)  # ≈ 20ms → 320
# 또는 INPUT_BLOCKSIZE = 512  # ≈ 32ms

# 스피커 출력 설정 (OpenAI TTS 기본값)
OUTPUT_SAMPLE_RATE = 24000
OUTPUT_CHANNELS = 1
OUTPUT_FORMAT = miniaudio.SampleFormat.SIGNED16


# --- 재생 안정화 파라미터 ---
PREBUFFER_MS = 300          # 재생 시작 전 최소 300ms 쌓기 (200~400 권장)
BYTES_PER_SAMPLE = 2 * OUTPUT_CHANNELS   # SIGNED16 mono → 2바이트

# 재생용 공유 버퍼
shared_pcm_buffer = bytearray()
shared_mp3_buffer = bytearray()

def ms_to_bytes(ms: int) -> int: 
    return int(OUTPUT_SAMPLE_RATE * BYTES_PER_SAMPLE * ms / 1000)

def try_decode_into_pcm(mp3_buffer: bytearray, pcm_buffer: bytearray) -> bool:
    """
    mp3_buffer에 누적된 바이트를 통째로 디코딩 시도.
    성공하면 mp3_buffer를 비우고 pcm_buffer에 PCM을 추가.
    실패(부분 프레임)면 그대로 둠.
    """
    if not mp3_buffer:
        return False
    try:
        decoded = miniaudio.decode(
            bytes(mp3_buffer),
            output_format=OUTPUT_FORMAT,
            nchannels=OUTPUT_CHANNELS,
            sample_rate=OUTPUT_SAMPLE_RATE,  # 필요시 리샘플
        )
        pcm_buffer.extend(decoded.samples)  # SIGNED16 bytes
        mp3_buffer.clear()
        return True
    except miniaudio.DecodeError:
        return False

def prefill_before_playback(sync_audio_queue, timeout_sec: float = 3.0):
    """
    재생 시작 전에 PREBUFFER_MS만큼 PCM을 확보(프리버퍼).
    timeout 내에 모자라면 있는 만큼으로 시작.
    """
    import time
    target = ms_to_bytes(PREBUFFER_MS)
    t0 = time.time()
    while len(shared_pcm_buffer) < target and (time.time() - t0) < timeout_sec:
        try:
            chunk = sync_audio_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        if chunk is None:
            break
        shared_mp3_buffer.extend(chunk)
        # 너무 잦은 디코딩을 피하려고 적당히 쌓였을 때만 시도(예: 12KB)
        if len(shared_mp3_buffer) >= 12 * 1024:
            try_decode_into_pcm(shared_mp3_buffer, shared_pcm_buffer)
    # 마지막으로 한 번 더 시도
    try_decode_into_pcm(shared_mp3_buffer, shared_pcm_buffer)
# --- 추가 끝 ---

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
                pcm_buffer = shared_pcm_buffer #bytearray()
                mp3_buffer = shared_mp3_buffer #bytearray() # MP3 청크를 모으기 위한 버퍼
                
                # 제너레이터를 .send() 호출에 대비시킴 (Priming)
                framecount = yield b''

                while True:
                    required_bytes = framecount *  BYTES_PER_SAMPLE
                    
                    while len(pcm_buffer) < required_bytes:
                        try:
                            mp3_chunk = sync_audio_queue.get(block=False)
                            if mp3_chunk is None:
                                # 스트림 종료 신호 → 마지막으로 디코드 시도 후 남은 거 재생
                                try_decode_into_pcm(mp3_buffer, pcm_buffer)
                                if pcm_buffer:
                                    yield bytes(pcm_buffer)  # 남은 오디오 재생
                                return

                            mp3_buffer.extend(mp3_chunk)
                            
                            if len(mp3_buffer) >= 12 * 1024:
                                try_decode_into_pcm(mp3_buffer, pcm_buffer)

                            ## 버퍼의 내용을 디코딩 시도
                            #try:
                                ## 수정: 디코딩 시 출력 형식 명시
                                #decoded = miniaudio.decode(bytes(mp3_buffer),
                                                           #output_format=OUTPUT_FORMAT,
                                                           #nchannels=OUTPUT_CHANNELS,
                                                           #sample_rate=OUTPUT_SAMPLE_RATE)
                                ## 성공하면 PCM 데이터를 pcm_buffer에 추가하고 mp3_buffer를 비움
                                #pcm_buffer.extend(decoded.samples)
                                #mp3_buffer.clear()
                            #except miniaudio.DecodeError:
                                ## 디코딩 실패. 데이터가 불완전할 수 있으므로 다음 청크를 기다림.
                                #pass

                        except queue.Empty:
                            # 큐가 비어있으면 루프를 빠져나가 조용한 오디오를 재생합니다.
                            break
                    
                    if len(pcm_buffer) < required_bytes:
                        try_decode_into_pcm(mp3_buffer, pcm_buffer)

                    if len(pcm_buffer) >= required_bytes:
                        output_chunk = pcm_buffer[:required_bytes]
                        del pcm_buffer[:required_bytes]
                        framecount = yield bytes(output_chunk)
                    else:
                        # 데이터가 부족하면 조용한 오디오로 메움(프리버퍼 덕에 빈도↓)
                        framecount = yield bytes(required_bytes)
                    
                    #if len(pcm_buffer) >= required_bytes:
                        #output_chunk = pcm_buffer[:required_bytes]
                        #del pcm_buffer[:required_bytes]
                        #framecount = yield bytes(output_chunk)
                    #else:
                        ## 데이터가 부족하면 조용한 오디오를 재생하여 끊김 방지
                        #silence = bytearray(required_bytes)
                        #framecount = yield silence
            
            # 태스크 실행
            recorder_task = asyncio.create_task(recorder())
            receiver_task = asyncio.create_task(receiver())
            
            await asyncio.to_thread(prefill_before_playback, sync_audio_queue)
            
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

