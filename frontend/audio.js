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
    this._activeSegment = null;    // per-segment buffer + VAD stats
    this._cycleTimer    = null;    // fires every 30s to rotate the recorder
    this._stopping      = false;   // true while stop() is in progress
    this._audioCtx      = null;
    this._sourceNode    = null;
    this._analyser      = null;
    this._vadBuffer     = null;
    this._vadRaf        = null;
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
    this._startVad();

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
    this._stopVad();
  }

  // ── Private ─────────────────────────────────────────────────────────────────

  _startSegment() {
    const mimeType = this._pickMimeType();
    const segment  = { chunks: [], voiceFrames: 0, hadVoice: false };
    this._activeSegment = segment;
    this._recorder = new MediaRecorder(this._stream, mimeType ? { mimeType } : {});
    this._mimeType = this._recorder.mimeType; // resolved actual mime type

    this._recorder.addEventListener("dataavailable", (e) => {
      if (e.data && e.data.size > 0) segment.chunks.push(e.data);
    });

    this._recorder.addEventListener("stop", () => {
      if (segment.chunks.length === 0) return;

      const blob = new Blob(segment.chunks, { type: this._mimeType });

      // Dispatch only when we have enough bytes and detected speech activity.
      if (blob.size > 1024 && segment.hadVoice) {
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

  _startVad() {
    if (!this._stream) return;

    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;

    this._audioCtx = new AudioCtx();
    this._sourceNode = this._audioCtx.createMediaStreamSource(this._stream);
    this._analyser = this._audioCtx.createAnalyser();
    this._analyser.fftSize = 2048;
    this._analyser.smoothingTimeConstant = 0.2;
    this._vadBuffer = new Float32Array(this._analyser.fftSize);
    this._sourceNode.connect(this._analyser);

    const SPEECH_RMS_THRESHOLD = 0.01;
    const MIN_VOICE_FRAMES = 3;

    const tick = () => {
      if (!this.isRecording || !this._analyser || !this._activeSegment) return;

      this._analyser.getFloatTimeDomainData(this._vadBuffer);
      let sum = 0;
      for (let i = 0; i < this._vadBuffer.length; i++) {
        const v = this._vadBuffer[i];
        sum += v * v;
      }
      const rms = Math.sqrt(sum / this._vadBuffer.length);

      if (rms > SPEECH_RMS_THRESHOLD) {
        this._activeSegment.voiceFrames += 1;
        if (this._activeSegment.voiceFrames >= MIN_VOICE_FRAMES) {
          this._activeSegment.hadVoice = true;
        }
      }

      this._vadRaf = requestAnimationFrame(tick);
    };

    this._vadRaf = requestAnimationFrame(tick);
  }

  _stopVad() {
    if (this._vadRaf) {
      cancelAnimationFrame(this._vadRaf);
      this._vadRaf = null;
    }

    if (this._sourceNode) {
      this._sourceNode.disconnect();
      this._sourceNode = null;
    }
    if (this._analyser) {
      this._analyser.disconnect();
      this._analyser = null;
    }
    this._vadBuffer = null;

    if (this._audioCtx) {
      this._audioCtx.close().catch(() => {});
      this._audioCtx = null;
    }
  }
}
