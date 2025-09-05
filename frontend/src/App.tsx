import { useState, useEffect, useRef } from "react";
import "./App.css";

// --- Constants ---
const WS_URL = "ws://localhost:3001/ws";
const TARGET_SR = 16000;             // 16 kHz
const TARGET_CH = 1;                 // mono
const FRAME_MS = 20;                 // 20ms → 320 samples → 640 bytes

// --- Enums and Types ---
enum ConnectionStatus {
  Disconnected = "DISCONNECTED",
  Connecting = "CONNECTING",
  Connected = "CONNECTED",
  Error = "ERROR",
}

enum RecordingStatus {
  Idle = "IDLE",
  Recording = "RECORDING",
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
}

// ---- Audio helpers (Float32 → Int16, resample, framing) ----
function float32ToInt16(buf: Float32Array): Int16Array {
  const out = new Int16Array(buf.length);
  for (let i = 0; i < buf.length; i++) {
    let s = buf[i];
    if (!Number.isFinite(s)) s = 0;
    if (s > 1) s = 1; else if (s < -1) s = -1;
    out[i] = (s < 0 ? s * 0x8000 : s * 0x7fff) | 0;
  }
  return out;
}

function resampleLinearMono(int16: Int16Array, fromSr: number, toSr: number): Int16Array {
  if (fromSr === toSr) return int16;
  const ratio = toSr / fromSr;
  const outLen = Math.max(1, Math.floor(int16.length * ratio));
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const src = i / ratio;
    const i0 = Math.floor(src);
    const i1 = Math.min(i0 + 1, int16.length - 1);
    const t = src - i0;
    out[i] = Math.round((1 - t) * int16[i0] + t * int16[i1]);
  }
  return out;
}

// --- Main App Component ---
function App() {
  // --- State ---
  const [connectionStatus, setConnectionStatus] = useState(ConnectionStatus.Disconnected);
  const [recordingStatus, setRecordingStatus] = useState(RecordingStatus.Idle);
  const [userTranscript, setUserTranscript] = useState("");
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([]);
  const [isWebSocketReady, setIsWebSocketReady] = useState(false);

  // --- Refs ---
  const ws = useRef<WebSocket | null>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const audioQueue = useRef<ArrayBuffer[]>([]);
  const isPlaying = useRef(false);

  // capture graph refs
  const inputSampleRate = useRef<number>(48000);
  const sourceNode = useRef<MediaStreamAudioSourceNode | null>(null);
  const procNode = useRef<ScriptProcessorNode | null>(null);
  const muteNode = useRef<GainNode | null>(null);
  const stopHandles = useRef<() => void>(() => {});

  // framing
  const FRAME_SAMPLES = Math.round((TARGET_SR * FRAME_MS) / 1000); // 320
  const accFrame = useRef<Int16Array | null>(null);
  const accWrite = useRef<number>(0);

  // --- WebSocket Connection ---
  useEffect(() => {
    console.log("[Frontend] Setting up WebSocket connection...");
    setConnectionStatus(ConnectionStatus.Connecting);

    const socket = new WebSocket(WS_URL);
    socket.binaryType = 'arraybuffer';
    ws.current = socket;

    socket.onopen = () => {
      console.log("[Frontend] WebSocket connection established successfully.");
      setConnectionStatus(ConnectionStatus.Connected);
      setIsWebSocketReady(true);

      // Declare the audio format we will send (PCM16LE 16k mono frames)
      socket.send(JSON.stringify({
        type: "audio_meta",
        encoding: "pcm16le",
        sample_rate: TARGET_SR,
        channels: TARGET_CH,
      }));
    };

    socket.onmessage = handleWebSocketMessage;

    socket.onclose = (event) => {
      console.error(`[Frontend] WebSocket connection closed. Code: ${event.code}, Reason: ${event.reason}`);
      setConnectionStatus(ConnectionStatus.Disconnected);
      setIsWebSocketReady(false);
    };

    socket.onerror = (error) => {
      console.error("[Frontend] WebSocket connection error:", error);
      setConnectionStatus(ConnectionStatus.Error);
      setIsWebSocketReady(false);
    };

    // Cleanup function
    return () => {
      console.log("[Frontend] Cleaning up WebSocket connection.");
      try { socket.close(); } catch {}
      stopStreaming();
    };
  }, []);

  // --- Audio Playback ---
  const initializeAudio = async () => {
    if (!audioContext.current) {
      audioContext.current = new AudioContext();
    }
    if (audioContext.current.state === 'suspended') {
      await audioContext.current.resume();
    }
    console.log('AudioContext state:', audioContext.current.state);
  };

  const playNextAudioChunk = async () => {
    if (audioQueue.current.length === 0) {
      isPlaying.current = false;
      return;
    }
    isPlaying.current = true;
    const arrayBuffer = audioQueue.current.shift()!;
    const audioBuffer = await audioContext.current!.decodeAudioData(arrayBuffer);
    const source = audioContext.current!.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioContext.current!.destination);
    source.start();
    source.onended = playNextAudioChunk;
  };

  const addToAudioQueue = (arrayBuffer: ArrayBuffer) => {
    audioQueue.current.push(arrayBuffer);
    if (!isPlaying.current) {
      playNextAudioChunk();
    }
  };

  // --- WebSocket Message Handling ---
  const handleWebSocketMessage = async (event: MessageEvent) => {
    if (event.data instanceof Blob) {
      const arrayBuffer = await event.data.arrayBuffer();
      await initializeAudio();
      addToAudioQueue(arrayBuffer);
      return;
    }

    try {
      const message = JSON.parse(event.data);
      switch (message.type) {
        case "stt_interim_result":
          setUserTranscript(message.transcript);
          break;
        case "chat_message":
          setChatHistory((prev) => [...prev, message]);
          if (message.role === "user") {
            setUserTranscript("");
          }
          break;
        default:
          // ignore unknown message types
          break;
      }
    } catch (error) {
      // Non-JSON text frames are ignored
    }
  };

  // --- PCM streaming (replaces MediaRecorder) ---
  const startStreaming = async () => {
    if (recordingStatus === RecordingStatus.Recording) return;
    if (!ws.current || ws.current.readyState !== WebSocket.OPEN) {
      alert('WebSocket connection not ready. Please wait.');
      return;
    }

    await initializeAudio();

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      const ac = new (window.AudioContext || (window as any).webkitAudioContext)();
      inputSampleRate.current = ac.sampleRate; // likely 44100/48000

      const src = ac.createMediaStreamSource(stream);
      const proc = ac.createScriptProcessor(4096, 1, 1); // mono in/out

      // ensure onaudioprocess fires: connect into graph but keep muted
      const mute = ac.createGain();
      mute.gain.value = 0.0;

      src.connect(proc);
      proc.connect(mute);
      mute.connect(ac.destination);

      accFrame.current = null;
      accWrite.current = 0;

      proc.onaudioprocess = (e) => {
        if (!ws.current || ws.current.readyState !== WebSocket.OPEN) return;
        const f32 = e.inputBuffer.getChannelData(0);
        const i16raw = float32ToInt16(f32);
        const i16 = resampleLinearMono(i16raw, inputSampleRate.current, TARGET_SR);

        let offset = 0;
        while (offset < i16.length) {
          if (!accFrame.current) {
            accFrame.current = new Int16Array(FRAME_SAMPLES);
            accWrite.current = 0;
          }
          const need = FRAME_SAMPLES - accWrite.current;
          const take = Math.min(need, i16.length - offset);
          accFrame.current.set(i16.subarray(offset, offset + take), accWrite.current);
          accWrite.current += take;
          offset += take;

          if (accWrite.current === FRAME_SAMPLES) {
            const bytes = new Uint8Array(accFrame.current.buffer.slice(0));
            try { ws.current.send(bytes); } catch {}
            accFrame.current = null;
            accWrite.current = 0;
          }
        }
      };

      // store handles for stop
      sourceNode.current = src;
      procNode.current = proc;
      muteNode.current = mute;
      audioContext.current = ac;

      stopHandles.current = () => {
        try { proc.disconnect(); } catch {}
        try { mute.disconnect(); } catch {}
        try { src.disconnect(); } catch {}
        stream.getTracks().forEach((t) => t.stop());
        try { ac.close(); } catch {}
      };

      setRecordingStatus(RecordingStatus.Recording);
      console.log('Recording (PCM16LE 16k mono) started.');
    } catch (error) {
      console.error("Failed to get media devices:", error);
      alert("Could not access microphone. Please check permissions.");
    }
  };

  const stopStreaming = () => {
    if (recordingStatus !== RecordingStatus.Recording) return;

    // flush last partial frame with zero padding
    if (accFrame.current && accWrite.current > 0 && ws.current && ws.current.readyState === WebSocket.OPEN) {
      const out = new Int16Array(FRAME_SAMPLES);
      out.set(accFrame.current.subarray(0, accWrite.current), 0);
      const bytes = new Uint8Array(out.buffer.slice(0));
      try { ws.current.send(bytes); } catch {}
    }

    try { stopHandles.current(); } catch {}

    // optional end-of-stream signal
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      try { ws.current.send(JSON.stringify({ type: "end_of_stream" })); } catch {}
    }

    setRecordingStatus(RecordingStatus.Idle);
    console.log('Recording stopped.');
  };

  const toggleRecording = () => {
    if (recordingStatus === RecordingStatus.Recording) {
      stopStreaming();
    } else {
      startStreaming();
    }
  };

  // --- UI Rendering ---
  return (
    <div className="container">
      <header>
        <h1>Voice AI Assistant</h1>
        <div className={`status-light ${connectionStatus.toLowerCase()}`} />
        <span className="status-text">{connectionStatus}</span>
      </header>

      <main className="chat-area">
        {chatHistory.map((msg) => (
          <div key={msg.id} className={`chat-bubble ${msg.role}`}>
            {msg.text}
          </div>
        ))}
        {userTranscript && (
          <div className="chat-bubble user interim">{userTranscript}</div>
        )}
      </main>

      <footer>
        <button
          className={`record-button ${recordingStatus.toLowerCase()}`}
          onClick={toggleRecording}
          disabled={connectionStatus !== ConnectionStatus.Connected || !isWebSocketReady}
        >
          {recordingStatus === RecordingStatus.Recording ? "Stop" : "Record"}
        </button>
      </footer>
    </div>
  );
}

export default App;
