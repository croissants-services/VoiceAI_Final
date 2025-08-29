import asyncio, json, os, time, queue, signal, sys
import numpy as np
import sounddevice as sd
import websockets

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")
DG_API_KEY = os.getenv("DEEPGRAM_API_KEY")
DG_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-2"
    "&language=ko"
    "&punctuate=true"
    "&interim_results=true"
    "&encoding=linear16"
    "&sample_rate=16000"
    "&channels=1"
)

SAMPLE_RATE = 16000
BLOCK_MS = 32                             # Audio block size in milliseconds
BLOCK_SAMPLES = SAMPLE_RATE * BLOCK_MS // 1000
CHANNELS = 1
DTYPE = "float32"

RMS_THRESHOLD = 0.015                      # RMS threshold for voice activity detection
START_FRAMES = 3                          # Frames above threshold to start speaking
SILENCE_FRAMES = 20                       # Frames below threshold to stop speaking


audio_q = queue.Queue()
speaking = False
above_cnt = 0
silent_cnt = 0

t_utter_start = None       # Start time of utterance (detected by VAD)
t_first_partial = None     # Time of first partial response
t_speech_end = None        # Time when VAD detects end of speech
prev_text = ""             # Previous partial text content (for diff analysis)

def log_token_like(appended: str):
    """Log newly appended partial text tokens with timing information"""
    global t_utter_start
    if not appended.strip():
        return
    # Split by spaces if present, otherwise split into characters
    tokens = appended.split() if " " in appended else list(appended)
    now_ms = (time.time() - (t_utter_start or time.time())) * 1000
    for tok in tokens:
        if tok.strip():
            print(f"[+token] {tok}  (+{now_ms:.0f} ms)")

def emit_diff(new_text: str):
    """Compare with prev_text and output only newly generated content"""
    global prev_text, t_first_partial, t_utter_start
    # Record first partial response time
    if t_first_partial is None and new_text:
        t_first_partial = time.time()
        if t_utter_start:
            print(f"[first_partial_latency] {(t_first_partial - t_utter_start)*1000:.0f} ms")

    # Calculate common prefix length
    prefix_len = 0
    for a, b in zip(prev_text, new_text):
        if a == b:
            prefix_len += 1
        else:
            break
    appended = new_text[prefix_len:]
    prev_text = new_text
    if appended:
        log_token_like(appended)

def audio_callback(indata, frames, time_info, status):
    """Process microphone input, convert to PCM16, and perform VAD"""
    global speaking, above_cnt, silent_cnt, t_utter_start, t_speech_end
    # indata: float32 [-1, 1]
    # Simple VAD based on RMS energy (for reference only)
    rms = float(np.sqrt(np.mean(indata[:, 0] ** 2)))
    if rms >= RMS_THRESHOLD:
        above_cnt += 1
        silent_cnt = 0
        if not speaking and above_cnt >= START_FRAMES:
            speaking = True
            t_utter_start = time.time()
            # Reset partial state for new utterance
            reset_partial_state(keep_prev=False)
            print("\n🎙️ speaking started")
    else:
        above_cnt = 0
        silent_cnt += 1
        if speaking and silent_cnt >= SILENCE_FRAMES:
            speaking = False
            t_speech_end = time.time()
            print("🔇 speaking ended (VAD)")

    # Convert audio to PCM16 format for Deepgram
    s= np.clip(indata[:, 0], -1.0, 1.0)
    pcm16 = (s * 32767).astype(np.int16).tobytes()

    try:
        audio_q.put_nowait(pcm16) 
    except queue.Full:
        # Drop oldest chunk if queue is full
        try:
            audio_q.get_nowait()
            audio_q.put_nowait(pcm16)
        except queue.Empty:
            pass

def reset_partial_state(keep_prev: bool):
    """Reset partial transcription state"""
    global prev_text, t_first_partial
    if not keep_prev:
        prev_text = ""
    t_first_partial = None

async def producer(ws):
    """Send audio data from microphone to WebSocket"""
    with sd.InputStream(
        channels=CHANNELS,
        samplerate=SAMPLE_RATE,
        dtype=DTYPE,
        callback=audio_callback,
        blocksize=BLOCK_SAMPLES,
    ):
        print("🎵️ Recording... (Ctrl+C to stop)")
        while True:
            chunk = await asyncio.to_thread(audio_q.get)
            await ws.send(chunk)

def parse_deepgram_json(message: str):
    """
    Parse Deepgram JSON response to extract transcript and is_final flag.
    Handles different response formats from various API versions.
    """
    try:
        data = json.loads(message)
    except Exception:
        return None, None

    # Standard format: { "channel": { "alternatives": [ { "transcript": "..." } ] }, "is_final": false }
    if isinstance(data, dict):
        if "channel" in data and isinstance(data["channel"], dict):
            alts = data["channel"].get("alternatives")
            if isinstance(alts, list) and alts:
                tr = alts[0].get("transcript", "")
                is_final = bool(data.get("is_final", False))
                return tr, is_final

        # Handle alternative response formats
        if "results" in data and isinstance(data["results"], list) and data["results"]:
            res0 = data["results"][0]
            alts = res0.get("alternatives", [])
            tr = alts[0].get("transcript", "") if alts else ""
            is_final = bool(res0.get("final", False))
            return tr, is_final

    return None, None

async def consumer(ws):
    """Process Deepgram responses: show partial diffs and final timing"""
    global t_utter_start, t_speech_end

    async for msg in ws:
        # Deepgram may send binary data (ping/keepalive messages)
        if isinstance(msg, (bytes, bytearray)):
            continue

        transcript, is_final = parse_deepgram_json(msg)
        if transcript is None:
            print("⚠️ non-json or unexpected:", msg[:200])  # Debug output
            continue

        # 실시간 partial diff 출력
        if transcript:
            emit_diff(transcript)

        # 최종 결과 처리
        if is_final and transcript:
            # 기본값으로 초기화 후, 계산 가능할 때만 채운다
            final_latency_ms = None
            if t_speech_end:
                final_latency_ms = (time.time() - t_speech_end) * 1000
                t_speech_end = None  # 한 번 쓴 뒤 리셋

            if t_utter_start:
                total_ms = (time.time() - t_utter_start) * 1000
                print(f"[FINAL] {transcript}  (total {total_ms:.0f} ms)")
            else:
                print(f"[FINAL] {transcript}")

            # final latency는 값이 있을 때만 출력
            if final_latency_ms is not None:
                print(f"[final_latency] {final_latency_ms:.0f} ms after speech ended")
            else:
                # 필요하면 디버그용 메시지(선택)
                # print("[final_latency] (speech_end not captured yet)")
                pass

            # 다음 발화를 위해 상태 리셋
            t_utter_start = time.time()
            reset_partial_state(keep_prev=False)


async def main():
    if not DG_API_KEY or DG_API_KEY == "YOUR_DEEPGRAM_API_KEY":
        print("❌ DEEPGRAM_API_KEY is not set. Please check environment variables and restart the program.")
        sys.exit(1)

    headers = [("Authorization", f"Token {DG_API_KEY}")]
    # Adjust buffer size with write_limit / max_size
    async with websockets.connect(
        DG_URL, additional_headers=[("Authorization", f"Token {DG_API_KEY}")], write_limit=2**20, max_size=2**20
    ) as ws:
        await asyncio.gather(producer(ws), consumer(ws))

if __name__ == "__main__":
    import sys, asyncio
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
