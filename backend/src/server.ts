// 서버 부트스트랩: /ws 게이트웨이 부착, 헬스체크, 그레이스풀 셧다운
import http from "http";
import { attachGateway } from "./websocket/gateway";

const WS_PATH = "/ws";

// 환경값 읽기 (기본값 포함)
const PORT = Number.parseInt(process.env.PORT ?? "3001", 10);
const MODEL_WS_URL = process.env.MODEL_WS_URL ?? "ws://127.0.0.1:8000/ws/s2s";

// 간단한 URL 유효성 검사 (ws/wss만 허용)
function isValidWsUrl(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "ws:" || u.protocol === "wss:";
  } catch {
    return false;
  }
}

const server = http.createServer((req, res) => {
  // 공통 헤더
  res.setHeader("X-Powered-By", "voiceai-bff");

  switch (req.url) {
    case "/health":
    case "/healthz": {
      // liveness: 프로세스가 살아있는지만 확인
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
      return;
    }
    case "/ready": {
      // readiness: 업스트림 URL 형식이 유효한지만 빠르게 체크 (실제 핸드셰이크는 WS 연결 시 수행)
      const ready = isValidWsUrl(MODEL_WS_URL);
      res.writeHead(ready ? 200 : 503, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: ready, upstream: MODEL_WS_URL }));
      return;
    }
    default: {
      res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("OK");
      return;
    }
  }
});

// 프록시 게이트웨이 부착 (클라이언트 ↔ FastAPI 중계)
attachGateway(server, MODEL_WS_URL);

// 서버 에러 로깅
server.on("error", (err) => {
  console.error("[BFF] server error:", err);
});

// keep-alive / 헤더 타임아웃 설정(옵션)
server.keepAliveTimeout = 75_000; // 75s (일부 프록시와 호환)
// ts-expect-error: Node 타입에 따라 존재하지 않을 수 있음
server.headersTimeout = 76_000;

// 기동
server.listen(PORT, () => {
  console.log(`[BFF] listening on http://localhost:${PORT}`);
  console.log(`[BFF] ws path: ${WS_PATH}  |  proxy → ${MODEL_WS_URL}`);
});

// 그레이스풀 셧다운
function shutdown(signal: NodeJS.Signals) {
  console.log(`[BFF] received ${signal}, shutting down...`);
  server.close((err) => {
    if (err) {
      console.error("[BFF] close error:", err);
      process.exit(1);
    }
    process.exit(0);
  });
  // 강제 종료 타이머 (보호)
  setTimeout(() => process.exit(1), 10_000).unref();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
