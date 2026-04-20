/**
 * app.js — State machine + UI orchestration.
 *
 * State:
 *   transcriptLines[]   — all timestamped lines captured so far
 *   suggestionBatches[] — array of { ts, suggestions[] }
 *   chatHistory[]       — { ts, role, content }
 *   apiKey              — from localStorage / settings panel
 *   settings            — prompt/model overrides (from settings panel)
 */

import { AudioRecorder } from "./audio.js";
import { transcribeAudio, fetchSuggestions, streamChat } from "./api.js";

// ── State ─────────────────────────────────────────────────────────────────────
let transcriptLines = [];       // { ts: "HH:MM:SS", text: string }
let suggestionBatches = [];     // { ts: string, suggestions: [] }
let chatHistory = [];           // { ts, role, content }
let isRecording = false;
let isSuggestLoading = false;
let isChatLoading = false;
let autoRefreshTimer = null;
let autoRefreshCountdown = 30;
let apiKey = localStorage.getItem("groq_api_key") || "";
let settings = JSON.parse(localStorage.getItem("app_settings") || "{}");

const AUTO_REFRESH_INTERVAL = 30; // seconds

// ── DOM refs ──────────────────────────────────────────────────────────────────
const micBtn          = document.getElementById("mic-btn");
const micStatus       = document.getElementById("mic-status");
const transcriptEl    = document.getElementById("transcript-list");
const suggestionsEl   = document.getElementById("suggestions-list");
const batchCountEl    = document.getElementById("batch-count");
const reloadBtn       = document.getElementById("reload-btn");
const countdownEl     = document.getElementById("countdown");
const chatMessagesEl  = document.getElementById("chat-messages");
const chatInput       = document.getElementById("chat-input");
const chatSendBtn     = document.getElementById("chat-send");
const exportBtn       = document.getElementById("export-btn");
const settingsBtn     = document.getElementById("settings-btn");
const settingsModal   = document.getElementById("settings-modal");
const settingsClose   = document.getElementById("settings-close");
const settingsSave    = document.getElementById("settings-save");
const apiKeyInput     = document.getElementById("settings-api-key");

// ── AudioRecorder ─────────────────────────────────────────────────────────────
const recorder = new AudioRecorder(async (blob, mimeType) => {
  if (!apiKey) return showError("Set your Groq API key in ⚙ Settings first.");
  try {
    const text = await transcribeAudio(blob, mimeType, apiKey);
    if (text) appendTranscriptLine(text);
  } catch (e) {
    showError("Transcription error: " + e.message);
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
    stopAutoRefresh();
  }
});

// ── Transcript ────────────────────────────────────────────────────────────────
function appendTranscriptLine(text) {
  const ts = nowTime();
  transcriptLines.push({ ts, text });

  const el = document.createElement("div");
  el.className = "transcript-line";
  el.innerHTML = `<span class="ts">${ts}</span> ${escHtml(text)}`;
  transcriptEl.appendChild(el);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────
function startAutoRefresh() {
  autoRefreshCountdown = AUTO_REFRESH_INTERVAL;
  updateCountdown();
  autoRefreshTimer = setInterval(() => {
    autoRefreshCountdown--;
    updateCountdown();
    if (autoRefreshCountdown <= 0) {
      autoRefreshCountdown = AUTO_REFRESH_INTERVAL;
      runSuggestions();
    }
  }, 1000);
}

function stopAutoRefresh() {
  clearInterval(autoRefreshTimer);
  countdownEl.textContent = "";
}

function updateCountdown() {
  countdownEl.textContent = `auto-refresh in ${autoRefreshCountdown}s`;
}

reloadBtn.addEventListener("click", () => {
  autoRefreshCountdown = AUTO_REFRESH_INTERVAL;
  runSuggestions();
});

// ── Suggestions ───────────────────────────────────────────────────────────────
async function runSuggestions() {
  if (isSuggestLoading || transcriptLines.length === 0) return;
  isSuggestLoading = true;
  reloadBtn.classList.add("loading");

  try {
    const lines = transcriptLines.map((l) => `${l.ts} ${l.text}`);
    const prevBatches = suggestionBatches.map((b) => b.suggestions);
    const suggestions = await fetchSuggestions(lines, prevBatches, apiKey, settings);
    const batch = { ts: nowTime(), suggestions };
    suggestionBatches.unshift(batch); // newest first
    renderSuggestions();
    batchCountEl.textContent = `${suggestionBatches.length} BATCH${suggestionBatches.length !== 1 ? "ES" : ""}`;
  } catch (e) {
    showError("Suggestions error: " + e.message);
  } finally {
    isSuggestLoading = false;
    reloadBtn.classList.remove("loading");
  }
}

function renderSuggestions() {
  suggestionsEl.innerHTML = "";

  suggestionBatches.forEach((batch, batchIdx) => {
    // Batch timestamp divider (not for the first/newest)
    const divider = document.createElement("div");
    divider.className = "batch-divider" + (batchIdx > 0 ? " old" : "");
    divider.textContent = `— BATCH ${suggestionBatches.length - batchIdx} · ${batch.ts} —`;
    suggestionsEl.appendChild(divider);

    batch.suggestions.forEach((s) => {
      const card = document.createElement("div");
      card.className = `suggestion-card type-${s.type.toLowerCase().replace(/_/g, "-")}`;
      card.innerHTML = `
        <div class="suggestion-type">${formatType(s.type)}</div>
        <div class="suggestion-preview">${escHtml(s.preview)}</div>
      `;
      card.addEventListener("click", () => handleSuggestionClick(s));
      suggestionsEl.insertBefore(card, divider.nextSibling || null);

      // Re-insert: cards before their own divider
      suggestionsEl.insertBefore(card, divider);
    });
    // Move divider after its cards
    suggestionsEl.appendChild(divider);
  });

  // Re-render properly: newest batch cards on top, then divider, then older
  // Simpler approach: rebuild cleanly
  suggestionsEl.innerHTML = "";
  suggestionBatches.forEach((batch, batchIdx) => {
    // Cards for this batch
    batch.suggestions.forEach((s) => {
      const card = document.createElement("div");
      card.className = `suggestion-card type-${s.type.toLowerCase().replace(/_/g, "-")}` + (batchIdx > 0 ? " old-batch" : "");
      card.innerHTML = `
        <div class="suggestion-type">${formatType(s.type)}</div>
        <div class="suggestion-preview">${escHtml(s.preview)}</div>
      `;
      card.addEventListener("click", () => handleSuggestionClick(s));
      suggestionsEl.appendChild(card);
    });

    // Batch divider after cards
    const divider = document.createElement("div");
    divider.className = "batch-divider";
    divider.textContent = `— BATCH ${suggestionBatches.length - batchIdx} · ${batch.ts} —`;
    suggestionsEl.appendChild(divider);
  });
}

function formatType(type) {
  const labels = {
    ANSWER: "Answer",
    FACT_CHECK: "Fact Check",
    QUESTION: "Question to Ask",
    TALKING_POINT: "Talking Point",
    CLARIFY: "Clarify",
  };
  return labels[type] || type;
}

// ── Chat ──────────────────────────────────────────────────────────────────────
async function handleSuggestionClick(suggestion) {
  const userMsg = suggestion.preview;
  addChatMessage("user", userMsg, suggestion.type);
  await sendChat(userMsg);
}

chatSendBtn.addEventListener("click", () => {
  const text = chatInput.value.trim();
  if (!text) return;
  chatInput.value = "";
  addChatMessage("user", text);
  sendChat(text);
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatSendBtn.click();
  }
});

function addChatMessage(role, content, type = null) {
  const ts = nowTime();
  chatHistory.push({ ts, role, content });

  const el = document.createElement("div");
  el.className = `chat-message ${role}`;

  if (role === "user") {
    const label = type ? `YOU · ${formatType(type).toUpperCase()}` : "YOU";
    el.innerHTML = `
      <div class="chat-label">${label}</div>
      <div class="chat-bubble">${escHtml(content)}</div>
    `;
  } else {
    el.innerHTML = `
      <div class="chat-label">ASSISTANT</div>
      <div class="chat-bubble" id="streaming-bubble"></div>
    `;
  }

  chatMessagesEl.appendChild(el);
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
  return el;
}

async function sendChat(userText) {
  if (isChatLoading || !apiKey) return;
  if (!apiKey) { showError("Set your Groq API key in ⚙ Settings."); return; }

  isChatLoading = true;
  chatSendBtn.disabled = true;

  // Build messages array from history (exclude the one we just added — it'll be last)
  const messages = chatHistory.map((m) => ({ role: m.role, content: m.content }));
  const lines = transcriptLines.map((l) => `${l.ts} ${l.text}`);

  // Add assistant placeholder
  const assistantEl = addChatMessage("assistant", "");
  const bubble = assistantEl.querySelector("#streaming-bubble");
  bubble.classList.add("streaming");

  let fullResponse = "";

  try {
    await streamChat(messages, lines, apiKey, (delta) => {
      fullResponse += delta;
      bubble.textContent = fullResponse;
      chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    }, settings);

    bubble.classList.remove("streaming");
    bubble.id = "";
    // Save to history
    chatHistory[chatHistory.length - 1] = { ts: nowTime(), role: "assistant", content: fullResponse };
  } catch (e) {
    bubble.classList.remove("streaming");
    bubble.id = "";
    bubble.textContent = "Error: " + e.message;
    bubble.classList.add("error");
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
  a.download = `meeting-session-${new Date().toISOString().slice(0, 19).replace(/:/g, "-")}.json`;
  a.click();
  URL.revokeObjectURL(url);
});

// ── Settings modal ────────────────────────────────────────────────────────────
function openSettings() {
  apiKeyInput.value = apiKey;

  // Populate all settings fields from current settings or defaults
  document.getElementById("s-suggestion-model").value = settings.suggestion_model || "llama-3.3-70b-versatile";
  document.getElementById("s-chat-model").value = settings.chat_model || "llama-3.3-70b-versatile";
  document.getElementById("s-suggestion-ctx").value = settings.suggestion_context_lines || 60;
  document.getElementById("s-chat-ctx").value = settings.chat_context_lines || 120;
  document.getElementById("s-suggestion-prompt").value = settings.suggestion_system_prompt || "";
  document.getElementById("s-chat-prompt").value = settings.chat_system_prompt || "";

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

  const overrides = {};
  const sm = document.getElementById("s-suggestion-model").value.trim();
  const cm = document.getElementById("s-chat-model").value.trim();
  const sc = parseInt(document.getElementById("s-suggestion-ctx").value);
  const cc = parseInt(document.getElementById("s-chat-ctx").value);
  const sp = document.getElementById("s-suggestion-prompt").value.trim();
  const cp = document.getElementById("s-chat-prompt").value.trim();

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
  return new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function escHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function showError(msg) {
  const el = document.getElementById("error-toast");
  el.textContent = msg;
  el.classList.add("visible");
  setTimeout(() => el.classList.remove("visible"), 5000);
}

// ── Init ──────────────────────────────────────────────────────────────────────
// Prompt for API key on first load if not set
if (!apiKey) {
  setTimeout(openSettings, 400);
}
