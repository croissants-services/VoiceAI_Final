import { useState, useEffect, useRef } from "react";
import "./App.css";

// --- Constants ---
const WS_URL = "ws://localhost:3001/ws";
const MEDIA_RECORDER_TIMESLICE = 100; // ms

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
  const mediaRecorder = useRef<MediaRecorder | null>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const audioQueue = useRef<ArrayBuffer[]>([]);
  const isPlaying = useRef(false);

  // --- WebSocket Connection ---
  useEffect(() => {
    console.log("[Frontend] Setting up WebSocket connection...");
    setConnectionStatus(ConnectionStatus.Connecting);

    const socket = new WebSocket(WS_URL);
    socket.binaryType = 'arraybuffer'; // 设置二进制数据类型
    ws.current = socket;

    socket.onopen = () => {
      console.log("[Frontend] WebSocket connection established successfully.");
      setConnectionStatus(ConnectionStatus.Connected);
      setIsWebSocketReady(true);
      
      // 发送测试数据确认连接
      const testBlob = new Blob(['test'], { type: 'audio/webm' });
      socket.send(testBlob);
      console.log("Test blob sent");
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
      socket.close();
      mediaRecorder.current?.stop();
    };
  }, []);

  // --- Audio Playback ---
  const initializeAudio = async () => {
    if (!audioContext.current) {
      audioContext.current = new AudioContext();
    }
    
    // 确保AudioContext处于运行状态
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
            setUserTranscript(""); // Clear interim transcript after user message is final
          }
          break;
        default:
          console.warn("Unknown message type:", message.type);
      }
    } catch (error) {
      console.error("Failed to parse WebSocket message:", error);
    }
  };

  // --- Recording Logic ---
  const startRecording = async () => {
    if (recordingStatus === RecordingStatus.Recording) return;
    
    if (!isWebSocketReady) {
      console.log('WebSocket not ready yet');
      alert('WebSocket connection not ready. Please wait.');
      return;
    }

    await initializeAudio();
    console.log("audio initialized");
    
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      
      console.log('Stream active:', stream.active);
      console.log('Audio tracks:', stream.getAudioTracks());
      console.log('Audio track enabled:', stream.getAudioTracks()[0]?.enabled);
      console.log('Audio track muted:', stream.getAudioTracks()[0]?.muted);
      
      // 尝试不同的编码选项，降级处理
      let options = {};
      if (MediaRecorder.isTypeSupported('audio/webm; codecs=opus')) {
        options = { mimeType: "audio/webm; codecs=opus" };
      } else if (MediaRecorder.isTypeSupported('audio/webm')) {
        options = { mimeType: "audio/webm" };
      } else {
        console.warn('Using default MediaRecorder options');
      }
      
      mediaRecorder.current = new MediaRecorder(stream, options);
      console.log('MediaRecorder created with mimeType:', mediaRecorder.current.mimeType);

      mediaRecorder.current.ondataavailable = async (event) => {
        console.log('MediaRecorder data available:', event.data.size, 'type:', event.data.type);
        console.log('WebSocket state:', ws.current?.readyState);
        
        if (event.data.size > 0 && ws.current?.readyState === WebSocket.OPEN) {
          try {
            // 方法1：直接发送Blob
            // console.log('Sending Blob to WebSocket...');
            // ws.current.send(event.data);
            
            // 方法2：如果上面不行，改为发送ArrayBuffer
            const arrayBuffer = await event.data.arrayBuffer();
            console.log('Converting to ArrayBuffer size:', arrayBuffer.byteLength);
            ws.current.send(arrayBuffer);
            
            console.log('Audio data sent successfully');
          } catch (error) {
            console.error('Error sending audio data:', error);
          }
        } else {
          if (event.data.size === 0) {
            console.warn('Empty audio chunk received');
          }
          if (ws.current?.readyState !== WebSocket.OPEN) {
            console.warn('WebSocket not ready, state:', ws.current?.readyState);
          }
        }
      };

      mediaRecorder.current.onstart = () => {
        console.log('MediaRecorder started');
      };

      mediaRecorder.current.onstop = () => {
        console.log('MediaRecorder stopped');
        // Send a final empty chunk to signal end of stream
        if (ws.current?.readyState === WebSocket.OPEN) {
          ws.current.send(JSON.stringify({ type: "end_of_stream" }));
        }
        setRecordingStatus(RecordingStatus.Idle);
      };

      mediaRecorder.current.onerror = (event) => {
        console.error('MediaRecorder error:', event);
      };

      mediaRecorder.current.start(MEDIA_RECORDER_TIMESLICE);
      setRecordingStatus(RecordingStatus.Recording);
      console.log('Recording started with timeslice:', MEDIA_RECORDER_TIMESLICE);
      
    } catch (error) {
      console.error("Failed to get media devices:", error);
      alert("Could not access microphone. Please check permissions.");
    }
  };

  const stopRecording = () => {
    if (mediaRecorder.current && recordingStatus === RecordingStatus.Recording) {
      mediaRecorder.current.stop();
    }
  };

  const toggleRecording = () => {
    if (recordingStatus === RecordingStatus.Recording) {
      stopRecording();
    } else {
      startRecording();
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
