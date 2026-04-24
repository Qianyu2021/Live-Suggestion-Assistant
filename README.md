# Live Suggestions Assistant

Real-time meeting copilot with 3 main loops:

- mic capture + transcription
- live suggestion cards from the latest transcript
- detailed chat answers when you click a card or type a question

The app is a single FastAPI service that also serves the frontend.

## What It Does

- records microphone audio in ~30-second chunks
- transcribes speech into a running transcript
- generates 3 live suggestions from recent context
- lets you click a suggestion to get a deeper answer in chat
- exports transcript, suggestion batches, and chat history as JSON

## Project Structure

- `backend/` FastAPI app, prompt logic, provider calls
- `frontend/` static HTML/CSS/JS UI
- `eval/` benchmark fixtures and manual evaluation templates
- `scripts/` export and benchmark helpers

## Requirements

- Python 3.11+ recommended
- a modern browser with microphone permission enabled
- an LLM API key usable by this app

This project currently sends requests through the Groq Python SDK, and the UI label says `Groq API Key`.

## Install

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## Run Locally

From the project root:

```bash
cd backend
python3 -m uvicorn main:app --reload --port 8000
```

Then open:

```text
http://localhost:8000
```

The FastAPI app serves both:

- the backend API
- the frontend website

## First-Time Setup In The UI

1. Open `http://localhost:8000`
2. Click `Settings`
3. Paste your API key into `Groq API Key`
4. Choose the suggestion model
5. Choose the chat model
6. Save
7. Click the mic button and allow microphone access in the browser

If no key is saved yet, the settings modal opens automatically.

## Model Settings

The UI lets you choose separate models for:

- `Suggestion Model`
- `Chat Model`

Current code defaults are:

- `llama-3.3-70b-versatile` for suggestions
- `llama-3.3-70b-versatile` for chat

The settings panel can override those values per browser session via local storage.

Examples you can try:

- `llama-3.3-70b-versatile`
- `openai/gpt-oss-120b`

Use models that are available to your provider account. If a model name is unsupported, the app will return an API error in the UI.

## How To Use The App

1. Start recording with the mic button
2. Speak or play meeting audio near your mic
3. Watch transcript lines appear in the left column
4. Read the live suggestions in the middle column
5. Click a suggestion to get a more detailed answer in the right column
6. Or type your own question in chat
7. Export the session with `Export session`

Notes:

- suggestion previews are meant to be useful even before clicking
- clicking gives more detail and next-step guidance
- chat is session-only
- for a new topic, refresh the page to start clean

## Suggested Local Workflow

- keep the app open at `http://localhost:8000`
- save your API key once in Settings
- test with short spoken clips first
- use `Reload suggestions` if you want a fresh batch immediately
- export sessions you want to benchmark later

## Troubleshooting

### The page opens but nothing works

Check that the backend server is running on port `8000`.

### The app asks for an API key

Open `Settings` and add your key. The frontend sends the key with each request.

### The mic does not start

Check:

- browser microphone permission
- macOS / Windows system microphone permission
- that you are using `http://localhost:8000` in a supported browser

### Suggestions or chat return an API error

Common causes:

- invalid API key
- unsupported model name
- provider-side rate limits
- transient provider failure

### Audio upload too large

The backend rejects audio over about `26 MB`.

## Deploy

The repo includes `render.yaml` for Render deployment.

Important env var:

- `GROQ_API_KEY`

Render start command:

```bash
cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT
```

## Evaluation And Benchmarking

This project uses TwinMind as an offline benchmark and manual judge, not as a runtime dependency.

### Export A Session

From the app UI, click `Export session`.

This creates:

- `meeting-YYYY-MM-DDTHH-MM-SS.json`

### Build Manual Eval Cases

From project root:

```bash
python3 scripts/export_eval_cases.py meeting-*.json --out-dir eval/out --context-lines 24
```

Outputs:

- `eval/out/eval_cases.jsonl`
- `eval/out/twinmind_eval_packet.md`

### Compare Against TwinMind Benchmark

If you have a benchmark export such as `eval/fixtures/twinmind_benchmark/twinmind_output.json`, run:

```bash
python3 scripts/evaluate_benchmark.py \
  <your-meeting-export.json> \
  eval/fixtures/twinmind_benchmark/twinmind_output.json \
  --out-dir eval/out
```

Outputs:

- `eval/out/benchmark_report.json`
- `eval/out/benchmark_report.md`

### Manual TwinMind Scoring

Use:

- `eval/TWINMIND_EVAL_TEMPLATE.md`

to score suggestion quality, clicked-answer quality, latency, code quality, and overall experience.
