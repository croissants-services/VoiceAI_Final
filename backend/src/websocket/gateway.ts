// 클라이언트 ↔ FastAPI 모델 서버 중계(프록시) 게이트웨이

import { WebSocketServer, WebSocket } from "ws";
import { ModelApiService } from "../services/ModelApiService";

export function attachGateway(server: any, upstreamUrl: string) {
  const wss = new WebSocketServer({ server, path: "/ws" });

  wss.on("connection", async (client: WebSocket) => {
    // 세션당 FastAPI와 1:1 연결
    const upstream = new ModelApiService(upstreamUrl);

    try {
      await upstream.connect();
    } catch {
      client.close(1011, "Upstream connect error");
      return;
    }

    // 업스트림 → 클라이언트
    upstream.onBinary = (buf) => client.send(buf, { binary: true });
    upstream.onJson   = (obj) => client.send(JSON.stringify(obj));
    upstream.onClose  = () => client.close(1011, "Upstream closed");

    // 클라이언트 → 업스트림
    client.on("message", (data, isBinary) => {
      if (isBinary) {
        upstream.sendBinary(data as Buffer); // 예: PCM16 chunk
      } else {
        try { upstream.sendJson(JSON.parse(String(data))); }
        catch { /* 유효하지 않은 JSON은 무시 */ }
      }
    });

    // 어느 한쪽이 닫히면 반대쪽도 정리
    client.on("close", () => upstream.close());
  });
}