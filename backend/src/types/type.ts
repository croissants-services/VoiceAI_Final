// src/types/type.ts
export type Envelope =
  | AudioEnvelope
  | ControlEnvelope;

export interface BaseEnvelope {
  kind: "audio" | "control";
  version: 1;
  ts: number;            // epoch millis
  id?: string;           // 선택: trace id
}

export interface AudioEnvelope extends BaseEnvelope {
  kind: "audio";
  payload: {
    encoding: "pcm16le"; // 고정(권장): PCM16LE
    sample_rate: 16000;  // 고정(권장): 16kHz
    channels: 1;         // 고정(권장): mono
    data_b64: string;    // 오디오 base64 (항상 이 필드만 사용)
    seq?: number;        // 선택: chunk sequence
  };
}

export interface ControlEnvelope extends BaseEnvelope {
  kind: "control";
  payload: {
    cmd:
      | "ping"
      | "start"
      | "stop"
      | "flush"
      | "format";       // 등 필요 시 확장
    args?: Record<string, any>;
  };
}

// ── (선택) 아주 가벼운 런타임 가드 ────────────────────────
export function isEnvelope(x: any): x is Envelope {
  if (!x || typeof x !== "object") return false;
  if (x.version !== 1 || typeof x.ts !== "number") return false;
  if (x.kind === "audio") {
    const p = x.payload;
    return (
      p &&
      p.encoding === "pcm16le" &&
      p.sample_rate === 16000 &&
      p.channels === 1 &&
      typeof p.data_b64 === "string"
    );
  }
  if (x.kind === "control") {
    const p = x.payload;
    return p && typeof p.cmd === "string";
  }
  return false;
}