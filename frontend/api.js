/**
 * api.js — Typed fetch wrappers for all backend endpoints.
 * All functions throw on non-2xx or on Groq errors.
 */

const BASE = "http://localhost:8000";

async function parseErrorResponse(res, fallback) {
  const text = await res.text().catch(() => "");
  if (!text) return fallback;
  try {
    const data = JSON.parse(text);
    return data.detail || data.error || fallback;
  } catch {
    return text.slice(0, 240) || fallback;
  }
}

/**
 * POST /api/transcribe
 * @param {Blob} audioBlob
 * @param {string} mimeType
 * @param {string} apiKey
 * @returns {Promise<string>} transcript text
 */
export async function transcribeAudio(audioBlob, mimeType, apiKey) {
  const form = new FormData();
  form.append("audio", audioBlob, "chunk.webm");
  form.append("api_key", apiKey);

  const res = await fetch(`${BASE}/api/transcribe`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await parseErrorResponse(res, "Transcription failed"));
  const data = await res.json().catch(() => ({}));
  return data.text || "";
}

/**
 * POST /api/suggest
 * @param {string[]} transcriptLines
 * @param {Array[]}  previousSuggestions  list of past batches
 * @param {string}   apiKey
 * @param {Object}   settings             optional overrides
 * @returns {Promise<Array>} array of { type, preview, detail_hint }
 */
export async function fetchSuggestions(transcriptLines, previousSuggestions, apiKey, settings = {}) {
  const res = await fetch(`${BASE}/api/suggest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      api_key: apiKey,
      transcript_lines: transcriptLines,
      previous_suggestions: previousSuggestions,
      settings,
    }),
  });
  if (!res.ok) throw new Error(await parseErrorResponse(res, "Suggestions failed"));
  const data = await res.json().catch(() => ({}));
  return data.suggestions || [];
}

/**
 * POST /api/chat  (SSE streaming)
 * @param {Array}    messages         full chat history [{role, content}]
 * @param {string[]} transcriptLines
 * @param {string}   apiKey
 * @param {function} onDelta          called with each string chunk
 * @param {Object}   settings
 * @returns {Promise<void>}  resolves when stream ends
 */
export async function streamChat(messages, transcriptLines, apiKey, onDelta, settings = {}) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      api_key: apiKey,
      messages,
      transcript_lines: transcriptLines,
      settings,
    }),
  });

  if (!res.ok) {
    throw new Error(await parseErrorResponse(res, "Chat failed"));
  }

  if (!res.body) {
    throw new Error("Chat stream unavailable (empty response body)");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop(); // keep incomplete line

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice(6).trim();
      if (payload === "[DONE]") return;
      try {
        const obj = JSON.parse(payload);
        if (obj.error) throw new Error(obj.error);
        if (obj.delta) onDelta(obj.delta);
      } catch (e) {
        if (e.message !== "Unexpected end of JSON input") throw e;
      }
    }
  }
}
