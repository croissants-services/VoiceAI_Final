// src/websocket/gateway.ts
import { WebSocketServer, WebSocket } from "ws";
import { ModelApiService } from "../services/ModelApiService";
import fs from "fs";
import path from "path";

/**
 * Goal
 * - Always forward AUDIO upstream as PCM16LE, 16kHz, mono
 * - Slice into ~20ms frames (default) for STT stability
 * - Pass control JSON as-is
 * - Accept binary (raw PCM/WAV/μ-law) or legacy JSON(base64) from client
 * - (Optional) Dump normalized audio to WAV per session for debugging
 */

// ====== Tunables (can be overridden by env) ======
const TARGET_SR = Number(process.env.BFF_AUDIO_TARGET_SR || 16000);
const TARGET_CH = 1; // fixed mono
const FRAME_MS  = Number(process.env.BFF_AUDIO_FRAME_MS || 20); // 20ms → 640B @16k mono
const DEFAULT_IN_ENCODING = (process.env.BFF_AUDIO_IN_ENCODING_DEFAULT || "pcm16le").toLowerCase(); // "pcm16le" | "mulaw" | "wav"
const RECORD_DIR = process.env.BFF_RECORD_DIR || ".recordings";
const ENABLE_RECORD = (process.env.BFF_RECORD_ENABLE || "1") !== "0";

// ====== Downlink tunables ======
const DOWN_MAX_BUFFERED = Number(process.env.BFF_DOWN_MAX_BUFFERED || 512 * 1024); // 512KB client ws buffer cap
const PING_INTERVAL_MS  = Number(process.env.BFF_PING_INTERVAL_MS  || 20000);      // 20s keepalive ping

// Derived
const FRAME_BYTES = Math.max(1, Math.floor((TARGET_SR * FRAME_MS) / 1000) * 2 /*bytes per sample*/ * TARGET_CH);

// Ensure record dir
(() => {
  try { if (ENABLE_RECORD && !fs.existsSync(RECORD_DIR)) fs.mkdirSync(RECORD_DIR, { recursive: true }); }
  catch (e) { console.warn("[BFF] cannot create record dir:", RECORD_DIR, e); }
})();

// ====== Helpers ======
const isWav = (buf: Buffer) =>
  buf.length >= 12 &&
  buf.toString("ascii", 0, 4) === "RIFF" &&
  buf.toString("ascii", 8, 12) === "WAVE";

function parseWavPcm16le(buf: Buffer): { data: Buffer; sampleRate: number; channels: number } | null {
  if (!isWav(buf)) return null;
  let offset = 12, sr = TARGET_SR, ch = TARGET_CH;
  let data: Buffer | null = null;
  while (offset + 8 <= buf.length) {
    const id = buf.toString("ascii", offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (id === "fmt ") {
      const fmt = buf.readUInt16LE(start + 0);
      ch = buf.readUInt16LE(start + 2);
      sr = buf.readUInt32LE(start + 4);
      const bps = buf.readUInt16LE(start + 14);
      if (fmt !== 1 || bps !== 16) return null; // PCM16LE only
    } else if (id === "data") {
      data = buf.slice(start, start + size);
    }
    offset = start + size;
  }
  if (!data) return null;
  return { data, sampleRate: sr, channels: ch };
}

// μ-law decode table
const MU_LAW_TABLE = (() => {
  const t = new Int16Array(256);
  for (let i = 0; i < 256; i++) {
    let mu = ~i & 0xff;
    let sign = (mu & 0x80) ? -1 : 1;
    let exponent = (mu >> 4) & 0x07;
    let mantissa = mu & 0x0f;
    let mag = ((mantissa << 3) + 0x84) << exponent;
    t[i] = sign * (mag - 0x84);
  }
  return t;
})();
const muLawToPcm16 = (buf: Buffer) => {
  const out = new Int16Array(buf.length);
  for (let i = 0; i < buf.length; i++) out[i] = MU_LAW_TABLE[buf[i]];
  return out;
};

const downmixStereoToMono = (int16: Int16Array): Int16Array => {
  if (int16.length % 2 !== 0) return int16;
  const out = new Int16Array(int16.length / 2);
  for (let i = 0, j = 0; i < int16.length; i += 2, j++) {
    out[j] = ((int16[i] + int16[i + 1]) / 2) | 0;
  }
  return out;
};

// Minimal linear resampler (mono)
function resamplePcm16Mono(int16: Int16Array, fromSr: number, toSr: number): Int16Array {
  if (fromSr === toSr) return int16;
  const ratio = toSr / fromSr;
  const outLen = Math.max(1, Math.floor(int16.length * ratio));
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const src = i / ratio;
    const i0 = Math.floor(src);
    const i1 = Math.min(i0 + 1, int16.length - 1);
    const t = src - i0;
    const v = (1 - t) * int16[i0] + t * int16[i1];
    out[i] = v < -32768 ? -32768 : v > 32767 ? 32767 : Math.round(v);
  }
  return out;
}

type AudioHints = { encoding?: string; sample_rate?: number; channels?: number };

function normalizeToStandardPcm(payload: Buffer, hints?: AudioHints): Buffer | null {
  try {
    const enc = (hints?.encoding || DEFAULT_IN_ENCODING).toLowerCase();
    // 1) WAV
    if (enc === "wav" || isWav(payload)) {
      const wav = parseWavPcm16le(payload);
      if (!wav) return null;
      let pcm = new Int16Array(wav.data.buffer, wav.data.byteOffset, wav.data.byteLength / 2);
      if (wav.channels === 2) pcm = downmixStereoToMono(pcm);
      if (wav.sampleRate !== TARGET_SR) pcm = resamplePcm16Mono(pcm, wav.sampleRate, TARGET_SR);
      return Buffer.from(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    }
    // 2) μ-law (telephony)
    if (["mulaw", "mu-law", "ulaw", "g711"].includes(enc)) {
      const fromSr = hints?.sample_rate || 8000;
      const pcm8 = muLawToPcm16(payload);
      const pcm16 = resamplePcm16Mono(pcm8, fromSr, TARGET_SR);
      return Buffer.from(pcm16.buffer, pcm16.byteOffset, pcm16.byteLength);
    }
    // 3) default: PCM16LE
    if (enc === "pcm16le" || enc === "") {
      let pcm = new Int16Array(payload.buffer, payload.byteOffset, payload.byteLength / 2);
      const fromCh = hints?.channels || 1;
      if (fromCh === 2) pcm = downmixStereoToMono(pcm);
      const fromSr = hints?.sample_rate || TARGET_SR;
      if (fromSr !== TARGET_SR) pcm = resamplePcm16Mono(pcm, fromSr, TARGET_SR);
      return Buffer.from(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    }
    console.warn("[BFF] unsupported encoding:", enc, "— dropping");
    return null;
  } catch (e) {
    console.warn("[BFF] normalizeToStandardPcm failed:", e);
    return null;
  }
}

function writeWavFile(filepath: string, pcm: Buffer, sampleRate = TARGET_SR, channels = 1) {
  const byteRate = sampleRate * channels * 2;
  const blockAlign = channels * 2;
  const dataSize = pcm.length;
  const riffSize = 36 + dataSize;
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(riffSize, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(byteRate, 28);
  header.writeUInt16LE(blockAlign, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(dataSize, 40);
  fs.writeFileSync(filepath, Buffer.concat([header, pcm]));
}

export function attachGateway(server: any, upstreamUrl: string) {
  const wss = new WebSocketServer({ server, path: "/ws", perMessageDeflate: false });
  console.log("[BFF] ws path: /ws  |  proxy →", upstreamUrl);

  wss.on("connection", async (client: WebSocket, req) => {
    console.log("[BFF] client connected", req.socket.remoteAddress, req.socket.remotePort);

    // Keepalive state and helpers
    let pingTimer: NodeJS.Timeout | null = null;
    let clientAlive = true;
    const startKeepalive = () => {
      stopKeepalive();
      pingTimer = setInterval(() => {
        if (!clientAlive) {
          try { client.terminate(); } catch {}
          stopKeepalive();
          return;
        }
        clientAlive = false;
        try { client.ping(); } catch {}
      }, PING_INTERVAL_MS);
    };
    const stopKeepalive = () => { if (pingTimer) { clearInterval(pingTimer); pingTimer = null; } };
    client.on("pong", () => { clientAlive = true; });

    startKeepalive();

    // Per-session upstream
    const upstream = new ModelApiService(upstreamUrl);
    try {
      await upstream.connect();
    } catch (e) {
      console.error("[BFF] upstream connect error:", e);
      try { client.close(1011, "Upstream connect error"); } catch {}
      stopKeepalive();
      return;
    }

    // --- per-session state ---
    // Incoming audio hints (can be updated via control JSON 'audio_meta')
    let sessionHints: AudioHints = { encoding: DEFAULT_IN_ENCODING, sample_rate: TARGET_SR, channels: TARGET_CH };
    // Accumulator for frame alignment (avoid 12B/324B fragments)
    let frameAcc = Buffer.alloc(0);
    // Optional recording of normalized PCM
    const wavPath = path.join(RECORD_DIR, `session_${Date.now()}.wav`);
    let accPcm = Buffer.alloc(0);
    const record = (buf: Buffer) => { if (ENABLE_RECORD) accPcm = Buffer.concat([accPcm, buf]); };

    // Downlink coalescer
    let downAcc = Buffer.alloc(0);
    let downTimer: NodeJS.Timeout | null = null;
    const flushDownlink = () => {
      if (downAcc.length === 0) return;
      if (client.readyState !== WebSocket.OPEN) { downAcc = Buffer.alloc(0); return; }
      // If client buffer is too full, skip this cycle and try next tick (coalesce more)
      if ((client as any).bufferedAmount && (client as any).bufferedAmount > DOWN_MAX_BUFFERED) {
        return; // backpressure: hold off sending to avoid memory growth
      }
      try { client.send(downAcc, { binary: true }); } catch {}
      downAcc = Buffer.alloc(0);
    };
    const scheduleFlush = () => {
      if (downTimer) return;
      downTimer = setTimeout(() => {
        flushDownlink();
        downTimer = null;
      }, 10);
    };

    function drainFrames(buf: Buffer) {
      frameAcc = Buffer.concat([frameAcc, buf]);
      while (frameAcc.length >= FRAME_BYTES) {
        const chunk = frameAcc.subarray(0, FRAME_BYTES);
        frameAcc = frameAcc.subarray(FRAME_BYTES);
        upstream.sendBinary(chunk);
        // keep logging minimal to avoid spam
        // console.log("[BFF] → upstream sendBinary bytes=", chunk.length);
      }
    }

    // upstream → client passthrough
    upstream.onBinary = (buf) => {
      downAcc = Buffer.concat([downAcc, buf]);
      scheduleFlush();
    };
    upstream.onJson   = (obj) => { try { client.send(JSON.stringify(obj)); } catch {} };
    upstream.onClose  = () => {
      flushDownlink();
      if (downTimer) { clearTimeout(downTimer); downTimer = null; }
      stopKeepalive();
      try { client.close(1011, "Upstream closed"); } catch {};
    };

    // client → upstream
    client.on("message", (data, isBinary) => {
      if (isBinary) {
        // Binary audio → normalize(using current sessionHints) → frame-align → send
        const normalized = normalizeToStandardPcm(data as Buffer, sessionHints);
        if (!normalized) { console.warn("[BFF] drop: normalize failed"); return; }
        record(normalized);
        drainFrames(normalized);
        return;
      }

      // Text JSON: control or legacy audio(base64)
      try {
        const obj = JSON.parse(String(data));

        // 0) audio meta (update session hints)
        if (obj?.type === "audio_meta") {
          // allow client to specify true source format
          sessionHints = {
            encoding: (obj?.encoding || sessionHints.encoding || DEFAULT_IN_ENCODING).toLowerCase(),
            sample_rate: obj?.sample_rate || obj?.sampleRate || sessionHints.sample_rate || TARGET_SR,
            channels: obj?.channels ?? sessionHints.channels ?? TARGET_CH,
          };
          try { client.send(JSON.stringify({ type: "ack", of: "audio_meta", sessionHints })); } catch {}
          return;
        }

        // 1) legacy audio base64 support
        const b64 = obj?.chunk_data || obj?.data || obj?.audio || obj?.audio_data || obj?.b64 || obj?.base64;
        if (typeof b64 === "string" && b64.length > 0) {
          const hints: AudioHints = {
            encoding: (obj?.encoding || obj?.codec || sessionHints.encoding || DEFAULT_IN_ENCODING).toLowerCase(),
            sample_rate: obj?.sample_rate || obj?.sampleRate || sessionHints.sample_rate,
            channels: obj?.channels ?? sessionHints.channels,
          };
          const raw = Buffer.from(b64, "base64");
          const normalized = normalizeToStandardPcm(raw, hints);
          if (!normalized) { console.warn("[BFF] drop: normalize(b64) failed"); return; }
          record(normalized);
          drainFrames(normalized);
          return;
        }

        // 2) control/other JSON passthrough
        upstream.sendJson(obj);
      } catch {
        // ignore invalid JSON
      }
    });

    client.on("close", () => {
      stopKeepalive();
      try {
        if (ENABLE_RECORD && accPcm.length > 0) {
          writeWavFile(wavPath, accPcm, TARGET_SR, TARGET_CH);
          console.log("[BFF] saved wav:", wavPath, "len=", accPcm.length, "bytes");
        } else if (ENABLE_RECORD) {
          console.log("[BFF] no audio to save for this session");
        }
      } catch (e) {
        console.error("[BFF] save wav failed:", e);
      }
      frameAcc = Buffer.alloc(0);
      upstream.close();
    });

    client.on("error", (e) => {
      stopKeepalive();
      console.error("[BFF] client error:", e);
    });
  });
}
