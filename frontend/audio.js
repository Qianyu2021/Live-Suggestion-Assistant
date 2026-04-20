/**
 * audio.js — Mic capture and chunking.
 *
 * Usage:
 *   const recorder = new AudioRecorder(onChunkReady);
 *   await recorder.start();
 *   recorder.stop();
 *
 * onChunkReady(blob, mimeType) is called every CHUNK_INTERVAL ms
 * with the audio data ready to POST to /api/transcribe.
 */

const CHUNK_INTERVAL_MS = 30_000; // 30 seconds

export class AudioRecorder {
  constructor(onChunkReady) {
    this._onChunkReady = onChunkReady;
    this._mediaRecorder = null;
    this._stream = null;
    this._intervalId = null;
    this.isRecording = false;
  }

  async start() {
    if (this.isRecording) return;

    this._stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });

    // Pick the best supported mime type
    const mimeType = this._pickMimeType();
    this._mediaRecorder = new MediaRecorder(this._stream, { mimeType });

    this._mediaRecorder.addEventListener("dataavailable", (e) => {
      if (e.data && e.data.size > 0) {
        this._onChunkReady(e.data, mimeType);
      }
    });

    // Start recording; request a chunk every CHUNK_INTERVAL_MS
    this._mediaRecorder.start(CHUNK_INTERVAL_MS);
    this.isRecording = true;
  }

  stop() {
    if (!this.isRecording) return;
    this._mediaRecorder?.stop();
    this._stream?.getTracks().forEach((t) => t.stop());
    this._mediaRecorder = null;
    this._stream = null;
    this.isRecording = false;
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
