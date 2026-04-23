/**
 * app.js — State machine + UI orchestration.
 *
 * State:
 *   transcriptLines[]   — { ts, text } all timestamped lines captured
 *   suggestionBatches[] — { ts, suggestions[] } newest first
 *   chatHistory[]       — { ts, role, content } for export only
 *   apiMessages[]       — { role, content } sent to the API (no timestamps)
 *   apiKey              — from localStorage / settings panel
 *   settings            — prompt/model overrides from settings panel
 */

import { AudioRecorder } from "./audio.js";
import { transcribeAudio, fetchSuggestions, streamChat } from "./api.js";

// ── State ─────────────────────────────────────────────────────────────────────
let transcriptLines    = [];   // { ts, text }
let suggestionBatches  = [];   // { ts, suggestions[] } — newest at index 0
let chatHistory        = [];   // { ts, role, content } — for export
let apiMessages        = [];   // { role, content }     — sent to LLM
let isRecording        = false;
let isSuggestLoading   = false;
let isChatLoading      = false;
let pendingTranscribes = 0;    // # of in-flight transcribe requests
let suggestPending     = false;// a refresh was requested but had to wait
let autoRefreshTimer   = null;
let autoRefreshCountdown = 30;
let lastSuggestedLineCount = 0; // how many transcript lines existed at last batch
let apiKey   = localStorage.getItem("groq_api_key") || "";
let settings = JSON.parse(localStorage.getItem("app_settings") || "{}");

const AUTO_REFRESH_INTERVAL = 30; // seconds
const MAX_TRANSCRIPT_LINES_FOR_SUGGEST = 60;
const MAX_PREV_BATCHES_FOR_SUGGEST = 6;
const MAX_TRANSCRIPT_LINES_FOR_CHAT = 60;
const MAX_API_MESSAGES_FOR_CHAT = 6;

// ── DOM refs ──────────────────────────────────────────────────────────────────
const micBtn         = document.getElementById("mic-btn");
const micStatus      = document.getElementById("mic-status");
const micColBadge    = document.getElementById("mic-col-badge");
const transcriptEl   = document.getElementById("transcript-list");
const suggestionsEl  = document.getElementById("suggestions-list");
const batchCountEl   = document.getElementById("batch-count");
const reloadBtn      = document.getElementById("reload-btn");
const countdownEl    = document.getElementById("countdown");
const chatMessagesEl = document.getElementById("chat-messages");
const transcriptHint = document.getElementById("transcript-hint");
const chatInput      = document.getElementById("chat-input");
const chatSendBtn    = document.getElementById("chat-send");
const exportBtn      = document.getElementById("export-btn");
const settingsBtn    = document.getElementById("settings-btn");
const settingsModal  = document.getElementById("settings-modal");
const settingsClose  = document.getElementById("settings-close");
const settingsSave   = document.getElementById("settings-save");
const apiKeyInput    = document.getElementById("settings-api-key");

function shouldAppendTranscript(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) return false;

  const normalized = trimmed
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!normalized || !/[a-z0-9]/.test(normalized)) return false;

  const silenceHallucinations = new Set([
    "thank you",
    "thanks",
    "thank you for watching",
    "thanks for watching",
    "see you next time",
    "bye",
    "goodbye",
    "you",
  ]);
  if (silenceHallucinations.has(normalized)) return false;
  if (normalized.startsWith("thank you for watching")) return false;

  return true;
}

// ── AudioRecorder ─────────────────────────────────────────────────────────────
const recorder = new AudioRecorder(async (blob, mimeType) => {
  if (!apiKey) return showError("Set your Groq API key in ⚙ Settings first.");
  pendingTranscribes++;
  try {
    const text = await transcribeAudio(blob, mimeType, apiKey);
    if (shouldAppendTranscript(text)) appendTranscriptLine(text);
  } catch (e) {
    showError("Transcription error: " + e.message);
  } finally {
    pendingTranscribes--;
    // If a refresh was waiting on transcription to land, kick it off now.
    if (suggestPending && pendingTranscribes === 0) {
      suggestPending = false;
      runSuggestions();
    }
  }
});

// ── Mic button ────────────────────────────────────────────────────────────────
micBtn.addEventListener("click", async () => {
  if (!apiKey) {
    showError("Set your Groq API key in ⚙ Settings first.");
    openSettings();
    return;
  }

  if (!isRecording) {
    try {
      await recorder.start();
      isRecording = true;
      micBtn.classList.add("recording");
      micBtn.setAttribute("aria-label", "Stop recording");
      micStatus.textContent = "Listening…";
      micColBadge.textContent = "LIVE";
      micColBadge.style.color = "#ef4444";
      startAutoRefresh();
    } catch (e) {
      showError("Mic error: " + e.message);
    }
  } else {
    recorder.stop();
    isRecording = false;
    micBtn.classList.remove("recording");
    micBtn.setAttribute("aria-label", "Start recording");
    micStatus.textContent = "Stopped. Click to resume.";
    micColBadge.textContent = "IDLE";
    micColBadge.style.color = "";
    stopAutoRefresh();
    refreshSuggestionsOnStop();
  }
});

// ── Transcript ────────────────────────────────────────────────────────────────
function appendTranscriptLine(text) {
  const ts = nowTime();
  const wasEmpty = transcriptLines.length === 0;
  transcriptLines.push({ ts, text });

  // Hide the instruction hint once real transcript starts appearing
  if (transcriptHint) transcriptHint.style.display = "none";

  const el = document.createElement("div");
  el.className = "transcript-line";
  el.innerHTML = `<span class="ts">${ts}</span> ${escHtml(text)}`;
  transcriptEl.appendChild(el);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;

  // Fire the very first batch immediately so the user gets value right away
  // instead of waiting up to 30s for the auto-refresh tick.
  if (wasEmpty && isRecording) {
    runSuggestions();
  }
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────
function startAutoRefresh() {
  autoRefreshCountdown = AUTO_REFRESH_INTERVAL;
  updateCountdown();
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  autoRefreshTimer = setInterval(() => {
    autoRefreshCountdown--;
    updateCountdown();
    if (autoRefreshCountdown <= 0) {
      autoRefreshCountdown = AUTO_REFRESH_INTERVAL;
      requestSuggestionsRefresh();
    }
  }, 1000);
}

function stopAutoRefresh() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  autoRefreshTimer = null;
  countdownEl.textContent = "";
}

function updateCountdown() {
  countdownEl.textContent = `auto-refresh in ${autoRefreshCountdown}s`;
}

/**
 * requestSuggestionsRefresh — central entry point used by both the timer
 * tick and the manual reload button.
 *
 * Behaviour:
 *  • If a transcription is mid-flight, defer until it lands (so we always
 *    suggest against the freshest possible transcript).
 *  • Otherwise run immediately.
 *  • Skips silently if no transcript yet, or if no new lines since the
 *    last batch (no point burning a Groq call on identical input).
 */
function requestSuggestionsRefresh(force = false) {
  if (transcriptLines.length === 0) return;
  if (!force && transcriptLines.length === lastSuggestedLineCount) return;

  if (!force && pendingTranscribes > 0) {
    suggestPending = true;
    return;
  }
  runSuggestions();
}

function refreshSuggestionsOnStop() {
  if (transcriptLines.length === 0) return;
  // Force a new batch on stop even if <30s since last auto-refresh.
  lastSuggestedLineCount = -1;
  // Wait for final in-flight transcript chunk so suggestions use freshest text.
  if (pendingTranscribes > 0) {
    suggestPending = true;
    return;
  }
  runSuggestions();
}

reloadBtn.addEventListener("click", () => {
  // Manual reload: reset the auto-refresh countdown so we don't double-fire
  autoRefreshCountdown = AUTO_REFRESH_INTERVAL;
  updateCountdown();
  // Force a refresh even if line count hasn't changed (user explicitly asked)
  lastSuggestedLineCount = -1;
  requestSuggestionsRefresh(true);
});

// ── Suggestions ───────────────────────────────────────────────────────────────
async function runSuggestions() {
  if (isSuggestLoading || transcriptLines.length === 0) return;
  isSuggestLoading = true;
  reloadBtn.classList.add("loading");

  // Snapshot the line count we're suggesting against
  const snapshotCount = transcriptLines.length;

  try {
    const lines = transcriptLines
      .slice(-MAX_TRANSCRIPT_LINES_FOR_SUGGEST)
      .map((l) => `${l.ts} ${l.text}`);
    const prevBatches = suggestionBatches
      .slice(0, MAX_PREV_BATCHES_FOR_SUGGEST)
      .map((b) => b.suggestions);
    const suggestions = await fetchSuggestions(lines, prevBatches, apiKey, settings);

    const batch = { ts: nowTime(), suggestions };
    suggestionBatches.unshift(batch); // newest first
    lastSuggestedLineCount = snapshotCount;
    renderSuggestions();
    batchCountEl.textContent = `${suggestionBatches.length} BATCH${suggestionBatches.length !== 1 ? "ES" : ""}`;
  } catch (e) {
    showError("Suggestions error: " + e.message);
  } finally {
    isSuggestLoading = false;
    reloadBtn.classList.remove("loading");
  }
}

/**
 * renderSuggestions — clean single-pass render.
 *
 * Layout (top → bottom):
 *   [BATCH N cards]  ← newest, full opacity
 *   — BATCH N · timestamp —
 *   [BATCH N-1 cards]  ← faded
 *   — BATCH N-1 · timestamp —
 *   ...
 */
function renderSuggestions() {
  suggestionsEl.innerHTML = "";

  suggestionBatches.forEach((batch, batchIdx) => {
    const isNewest = batchIdx === 0;

    // Render the 3 cards for this batch
    batch.suggestions.forEach((s) => {
      const typeClass = `type-${s.type.toLowerCase().replace(/_/g, "-")}`;
      const card = document.createElement("div");
      card.className = `suggestion-card ${typeClass}${isNewest ? "" : " old-batch"}`;
      card.innerHTML = `
        <div class="suggestion-type">${formatType(s.type)}</div>
        <div class="suggestion-preview">${escHtml(s.preview)}</div>
      `;
      card.addEventListener("click", () => handleSuggestionClick(s));
      suggestionsEl.appendChild(card);
    });

    // Batch timestamp divider below its cards
    const divider = document.createElement("div");
    divider.className = "batch-divider";
    divider.textContent = `— BATCH ${suggestionBatches.length - batchIdx} · ${batch.ts} —`;
    suggestionsEl.appendChild(divider);
  });
}

// Human-readable type labels matching the screenshot exactly
function formatType(type) {
  const labels = {
    ANSWER:        "Answer",
    FACT_CHECK:    "Fact Check",
    QUESTION:      "Question to Ask",
    TALKING_POINT: "Talking Point",
  };
  return labels[type] || type;
}

// ── Chat ──────────────────────────────────────────────────────────────────────

/**
 * Clicking a suggestion card:
 *  1. Shows "YOU · <TYPE>" bubble with the preview text
 *  2. Sends a richer, expanded-answer prompt to the chat model
 *     (includes the suggestion's detail_hint + full transcript context)
 *  3. Streams the response into "ASSISTANT" bubble
 */
async function handleSuggestionClick(suggestion) {
  if (isChatLoading) return;

  const userContent = suggestion.preview;
  appendChatBubble("user", userContent, suggestion.type);

  const expandedPrompt =
    `Card type: ${formatType(suggestion.type)}\n` +
    `Card: "${suggestion.preview}"\n` +
    `Hint: ${suggestion.detail_hint}\n\n` +
    `Write:\n` +
    `Detailed answer to: "${suggestion.preview}"\n` +
    `Then 2-4 short, specific paragraphs grounded in the transcript.\n` +
    `End with: Follow-up suggestion: <one actionable next line>.`;

  await sendChatMessage(expandedPrompt, userContent);
}

// Manual chat input
chatSendBtn.addEventListener("click", () => {
  const text = chatInput.value.trim();
  if (!text || isChatLoading) return;
  chatInput.value = "";
  appendChatBubble("user", text);
  sendChatMessage(text, text);
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatSendBtn.click();
  }
});

function appendChatBubble(role, content, type = null) {
  const ts = nowTime();
  chatHistory.push({ ts, role, content });

  const wrapper = document.createElement("div");
  wrapper.className = `chat-message ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  if (content) bubble.textContent = content;

  if (role === "user") {
    const label = document.createElement("div");
    label.className = "chat-label";
    label.textContent = type ? `YOU · ${formatType(type).toUpperCase()}` : "YOU";
    wrapper.appendChild(label);
  } else {
    const label = document.createElement("div");
    label.className = "chat-label";
    label.textContent = "ASSISTANT";
    wrapper.appendChild(label);
  }

  wrapper.appendChild(bubble);
  chatMessagesEl.appendChild(wrapper);
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
  return bubble;
}

async function sendChatMessage(apiContent, displayText) {
  if (!apiKey) { showError("Set your Groq API key in ⚙ Settings."); return; }
  isChatLoading = true;
  chatSendBtn.disabled = true;

  apiMessages.push({ role: "user", content: apiContent });

  const bubble = appendChatBubble("assistant", "Thinking...");
  bubble.classList.add("streaming");

  let fullResponse = "";
  let renderQueued = false;
  const renderStream = () => {
    renderQueued = false;
    bubble.innerHTML = escHtml(fullResponse).replace(/\n\n/g, "</p><p>").replace(/\n/g, "<br>");
    chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
  };

  try {
    const lines = transcriptLines
      .slice(-MAX_TRANSCRIPT_LINES_FOR_CHAT)
      .map((l) => `${l.ts} ${l.text}`);
    const messagesForApi = apiMessages.slice(-MAX_API_MESSAGES_FOR_CHAT);

    await streamChat(messagesForApi, lines, apiKey, (delta) => {
      fullResponse += delta;
      if (!renderQueued) {
        renderQueued = true;
        requestAnimationFrame(renderStream);
      }
    }, settings);

    if (renderQueued) renderStream();
    bubble.classList.remove("streaming");
    if (!fullResponse.trim()) bubble.textContent = "No response received.";
    apiMessages.push({ role: "assistant", content: fullResponse });
    chatHistory[chatHistory.length - 1].content = fullResponse;

  } catch (e) {
    bubble.classList.remove("streaming");
    bubble.textContent = "Error: " + e.message;
    bubble.classList.add("error");
    apiMessages.pop();
  } finally {
    isChatLoading = false;
    chatSendBtn.disabled = false;
  }
}

// ── Export ────────────────────────────────────────────────────────────────────
exportBtn.addEventListener("click", () => {
  const exportData = {
    exportedAt: new Date().toISOString(),
    transcript: transcriptLines,
    suggestionBatches: suggestionBatches.map((b) => ({
      ts: b.ts,
      suggestions: b.suggestions,
    })),
    chatHistory,
  };

  const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `meeting-${new Date().toISOString().slice(0, 19).replace(/:/g, "-")}.json`;
  a.click();
  URL.revokeObjectURL(url);
});

// ── Settings modal ────────────────────────────────────────────────────────────
function openSettings() {
  apiKeyInput.value = apiKey;
  document.getElementById("s-suggestion-model").value = settings.suggestion_model || "llama-3.3-70b-versatile";
  document.getElementById("s-chat-model").value        = settings.chat_model       || "llama-3.3-70b-versatile";
  document.getElementById("s-suggestion-ctx").value   = settings.suggestion_context_lines || 60;
  document.getElementById("s-chat-ctx").value          = settings.chat_context_lines      || 120;
  document.getElementById("s-suggestion-prompt").value = settings.suggestion_system_prompt || "";
  document.getElementById("s-chat-prompt").value       = settings.chat_system_prompt      || "";
  settingsModal.classList.add("open");
}

settingsBtn.addEventListener("click", openSettings);
settingsClose.addEventListener("click", () => settingsModal.classList.remove("open"));
settingsModal.addEventListener("click", (e) => {
  if (e.target === settingsModal) settingsModal.classList.remove("open");
});

settingsSave.addEventListener("click", () => {
  apiKey = apiKeyInput.value.trim();
  localStorage.setItem("groq_api_key", apiKey);

  const sm = document.getElementById("s-suggestion-model").value.trim();
  const cm = document.getElementById("s-chat-model").value.trim();
  const sc = parseInt(document.getElementById("s-suggestion-ctx").value);
  const cc = parseInt(document.getElementById("s-chat-ctx").value);
  const sp = document.getElementById("s-suggestion-prompt").value.trim();
  const cp = document.getElementById("s-chat-prompt").value.trim();

  const overrides = {};
  if (sm) overrides.suggestion_model = sm;
  if (cm) overrides.chat_model = cm;
  if (!isNaN(sc)) overrides.suggestion_context_lines = sc;
  if (!isNaN(cc)) overrides.chat_context_lines = cc;
  if (sp) overrides.suggestion_system_prompt = sp;
  if (cp) overrides.chat_system_prompt = cp;

  settings = overrides;
  localStorage.setItem("app_settings", JSON.stringify(settings));
  settingsModal.classList.remove("open");
});

// ── Utilities ─────────────────────────────────────────────────────────────────
function nowTime() {
  return new Date().toLocaleTimeString("en-US", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function showError(msg) {
  const el = document.getElementById("error-toast");
  el.textContent = msg;
  el.classList.add("visible");
  setTimeout(() => el.classList.remove("visible"), 5000);
}

// ── Init ──────────────────────────────────────────────────────────────────────
if (!apiKey) setTimeout(openSettings, 400);
