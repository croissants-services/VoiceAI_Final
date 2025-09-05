// FastAPI `/ws/s2s`와의 WebSocket 클라이언트 래퍼

import WebSocket from "ws";

export class ModelApiService {
  private ws?: WebSocket;
  private closed = false;

  constructor(private upstreamUrl: string) {}

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(this.upstreamUrl);
      this.ws = ws;

      ws.once("open", () => resolve());
      ws.once("error", (err) => reject(err));

      ws.on("close", () => {
        if (!this.closed) this.onClose?.();
      });

      // 수신: 바이너리/JSON 분기
      ws.on("message", (data, isBinary) => {
        if (isBinary) this.onBinary?.(data as Buffer);
        else {
          try { this.onJson?.(JSON.parse(String(data))); }
          catch { /* 유효하지 않은 JSON은 무시 */ }
        }
      });
    });
  }

  // 콜백 바인딩
  onBinary?: (buf: Buffer) => void;
  onJson?: (obj: unknown) => void;
  onClose?: () => void;

  // 송신
  sendBinary(buf: Buffer) {
    console.log("[BFF] Sending binary to model:", buf.length, "bytes");
    this.ws?.send(buf, { binary: true });
  }
  sendJson(obj: unknown) {
    this.ws?.send(JSON.stringify(obj));
  }

  close() {
    this.closed = true;
    this.ws?.close();
  }
}