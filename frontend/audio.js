/**
 * audio.js — Mic capture with clean 30-second chunking.
 *
 * Strategy:
 *   Instead of relying on MediaRecorder's built-in timeslice (which fires a
 *   partial chunk on stop() causing duplicates), we manually restart the
 *   MediaRecorder every 30 seconds. Each restart produces one complete,
 *   self-contained audio blob — no duplicates, no partial-chunk collisions.
 *
 * Usage:
 *   const recorder = new AudioRecorder(onChunkReady);
 *   await recorder.start();
 *   recorder.stop();
 *
 *   onChunkReady(blob, mimeType) fires with a complete 30-second chunk.
 */

const CHUNK_DURATION_MS = 30_000; // 30 seconds per chunk

export class AudioRecorder {
  constructor(onChunkReady) {
    this._onChunkReady  = onChunkReady;
    this._stream        = null;
    this._recorder      = null;
    this._chunks        = [];      // accumulate data for current segment
    this._cycleTimer    = null;    // fires every 30s to rotate the recorder
    this._stopping      = false;   // true while stop() is in progress
    this.isRecording    = false;
  }

  // ── Public ──────────────────────────────────────────────────────────────────

  async start() {
    if (this.isRecording) return;

    this._stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        sampleRate: 16000,   // Whisper works well at 16kHz
      },
      video: false,
    });

    this._stopping   = false;
    this.isRecording = true;

    this._startSegment();

    // Rotate every 30s: stop current segment (fires dataavailable + fires
    // our handler), then immediately start a fresh one.
    this._cycleTimer = setInterval(() => this._rotateSement(), CHUNK_DURATION_MS);
  }

  stop() {
    if (!this.isRecording) return;
    this.isRecording = false;
    this._stopping   = true;

    clearInterval(this._cycleTimer);
    this._cycleTimer = null;

    // Stopping the recorder fires one final dataavailable with any remaining
    // audio. Our handler will dispatch it if it's non-empty.
    this._recorder?.stop();
  }

  // ── Private ─────────────────────────────────────────────────────────────────

  _startSegment() {
    const mimeType = this._pickMimeType();
    this._chunks   = [];
    this._recorder = new MediaRecorder(this._stream, mimeType ? { mimeType } : {});
    this._mimeType = this._recorder.mimeType; // resolved actual mime type

    this._recorder.addEventListener("dataavailable", (e) => {
      if (e.data && e.data.size > 0) this._chunks.push(e.data);
    });

    this._recorder.addEventListener("stop", () => {
      if (this._chunks.length === 0) return;

      const blob = new Blob(this._chunks, { type: this._mimeType });
      this._chunks = [];

      // Only dispatch if we have meaningful audio (>1KB avoids silence-only chunks)
      if (blob.size > 1024) {
        this._onChunkReady(blob, this._mimeType);
      }
    });

    this._recorder.start();
  }

  _rotateSement() {
    if (!this.isRecording) return;
    // Stop current segment → fires "stop" → dispatches chunk
    this._recorder.stop();
    // Start fresh segment immediately
    this._startSegment();
  }

  _pickMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    return candidates.find((m) => MediaRecorder.isTypeSupported(m)) || "";
  }
}