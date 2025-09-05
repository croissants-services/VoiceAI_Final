// ws-smoke.ts
import WebSocket from "ws";

// BFF WebSocket 엔드포인트 (server.ts가 띄우는 /ws)
const BFF_WS = process.env.BFF_WS ?? "ws://127.0.0.1:3001/ws";

// 16kHz, 16-bit PCM, 1채널, 0.5초짜리 사인파(또는 거의 무음) 샘플 생성
function genPcm16k(durationSec = 0.5, hz = 440, amp = 5000) {
  const sr = 16000;
  const n = Math.floor(sr * durationSec);
  const buf = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) {
    const sample = Math.round(amp * Math.sin((2 * Math.PI * hz * i) / sr));
    buf.writeInt16LE(sample, i * 2);
  }
  return buf;
}

// base64 JSON 페이로드 만들어주기 (gateway.ts의 extractBase64FromJson 키들과 호환)
function makeAudioJsonBase64(b: Buffer) {
  const b64 = b.toString("base64");
  return JSON.stringify({
    type: "voicechunk",
    chunk_data: b64,     // gateway.ts가 우선 탐색하는 키 중 하나
    sequence: 1,
    note: "smoke-test base64 frame",
  });
}

(async () => {
  console.log("[SMOKE] connecting to BFF:", BFF_WS);
  const ws = new WebSocket(BFF_WS, { perMessageDeflate: false });

  ws.on("open", () => {
    console.log("[SMOKE] connected. sending frames...");

    // (A) 제어용 JSON (업스트림에 그대로 JSON으로 전달되어야 함)
    const control = JSON.stringify({ type: "ping", ts: Date.now() });
    ws.send(control);
    console.log("[SMOKE] sent control JSON");

    // (B) base64 오디오 JSON (BFF가 base64를 풀어 바이너리로 업스트림에 보내야 함)
    const pcm = genPcm16k(0.5);
    const audioJson = makeAudioJsonBase64(pcm);
    ws.send(audioJson);
    console.log("[SMOKE] sent base64 audio JSON (lenB64=%d)", audioJson.length);

    // (C) 바이너리 프레임 (BFF가 그대로 업스트림으로 바이너리 포워딩해야 함)
    const pcm2 = genPcm16k(0.5, 660);
    ws.send(pcm2, { binary: true });
    console.log("[SMOKE] sent binary audio buffer (bytes=%d)", pcm2.length);

    // (D) 유효하지 않은 텍스트(깨진 JSON) – BFF가 무시해야 정상
    ws.send("not-a-json");
    console.log("[SMOKE] sent invalid text (should be ignored by BFF)");
  });

  ws.on("message", (data, isBinary) => {
    if (isBinary) {
      console.log("[SMOKE] recv binary from BFF (bytes=%d)", (data as Buffer).length);
    } else {
      const text = data.toString();
      console.log("[SMOKE] recv text from BFF:", text.slice(0, 200));
    }
  });

  ws.on("close", (code, reason) => {
    console.log("[SMOKE] closed:", code, reason.toString());
  });

  ws.on("error", (err) => {
    console.error("[SMOKE] error:", err);
  });
})();