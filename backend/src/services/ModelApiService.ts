// src/services/ModelApiService.ts
import WebSocket from "ws";

export class ModelApiService {
  private url: string;
  private ws?: WebSocket;

  public onBinary?: (buf: Buffer) => void;
  public onJson?: (obj: any) => void;
  public onClose?: () => void;
  public onError?: (err: any) => void;

  constructor(url: string) { this.url = url; }

  async connect(): Promise<void> {
    this.ws = new WebSocket(this.url, { perMessageDeflate: false });
    console.log("[BFF] upstream connecting →", this.url);
    this.ws.on("open", () => console.log("[BFF] upstream WS open:", this.url));
    this.ws.on("close", (code, reason) => console.log("[BFF] upstream WS close", code, reason?.toString?.()));
    this.ws.on("error", (e) => console.error("[BFF] upstream WS error", e));
    await new Promise<void>((resolve, reject) => {
      this.ws!.once("open", () => resolve());
      this.ws!.once("error", (e) => reject(e));
    });

    this.ws.on("message", (data, isBinary) => {
      console.log("[BFF] upstream→ message", isBinary ? `binary ${(data as Buffer).length}B` : "json");
      if (isBinary) { this.onBinary?.(data as Buffer); return; }
      try { this.onJson?.(JSON.parse(String(data))); } catch {}
    });
    this.ws.on("close", () => this.onClose?.());
    this.ws.on("error", (e) => this.onError?.(e));
  }

  sendBinary(buf: Buffer) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    console.log("[BFF] → upstream sendBinary bytes=", buf.length);
    this.ws.send(buf, { binary: true });
  }
  sendJson(obj: any) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    try { console.log("[BFF] → upstream sendJson keys=", Object.keys(obj)); } catch {}
    this.ws.send(JSON.stringify(obj));
  }
  close() { try { this.ws?.close(); } catch {} }
}
