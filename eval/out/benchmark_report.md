# TwinMind Benchmark Report

- Batch pairs evaluated: `2`
- Overall score: `3.3` / 100
- Preview similarity: `25.4` / 100
- Detail similarity: `7.0` / 100
- Clicked-answer similarity: `13.0` / 100
- Standalone preview score: `95.8` / 100
- Type match rate: `66.7` / 100
- Unsupported numeric claims: `7`

## Batch Detail

### Candidate `13:33:50` vs Benchmark `13:33:50`

- `QUESTION` -> `ANSWER` | preview `0.443` | detail `0.104` | answer `0.117` | standalone `1.000`
  Candidate: What runtime guardrail strategies will you use to sanitize agent inputs and outputs?
  Benchmark: Guardrails are runtime safeguards that sanitize agent inputs and outputs to enforce safety, policy, and correctness.
- `TALKING_POINT` -> `TALKING_POINT` | preview `0.105` | detail `0.043` | answer `0.094` | standalone `1.000`
  Candidate: Implement input validation with OpenAI moderation API and enforce output schema via JSON schema checks.
  Benchmark: Guardrails are the runtime safety layer for both inbound prompts and outbound responses.
- `QUESTION` -> `QUESTION` | preview `0.195` | detail `0.068` | answer `0.124` | standalone `0.750`
  Candidate: What metric is currently the bottleneck in this discussion?
  Benchmark: What other runtime controls should be configured alongside system prompts and tool operations?

### Candidate `13:34:05` vs Benchmark `13:34:05`

- `QUESTION` -> `QUESTION` | preview `0.336` | detail `0.05` | answer `0.171` | standalone `1.000`
  Candidate: Which runtime guardrail mechanisms (e.g., content filtering, schema validation) will you enable?
  Benchmark: Which runtime defenses will you use against prompt injection, jailbreaks, and unsafe requests?
- `TALKING_POINT` -> `TALKING_POINT` | preview `0.162` | detail `0.134` | answer `0.157` | standalone `1.000`
  Candidate: OpenAI moderation API + JSON schema checks add ~10 ms latency per request while blocking unsafe content.
  Benchmark: Guardrails improve both safety and cost efficiency by blocking off-topic or adversarial requests early.
- `ANSWER` -> `FACT_CHECK` | preview `0.284` | detail `0.02` | answer `0.118` | standalone `1.000`
  Candidate: Guardrails are runtime safeguards that sanitize both incoming prompts and outgoing responses.
  Benchmark: Guardrails can significantly reduce prompt-injection risk, but they do not eliminate it on their own.

## Unsupported Numeric Claims

- Batch `13:33:50` `answer` on "What runtime guardrail strategies will you use to sanitize agent inputs and outputs?": 1, 2, 3, 10, 30, 1, 5
- Batch `13:33:50` `answer` on "What metric is currently the bottleneck in this discussion?": 120, 8, 115, 6
- Batch `13:34:05` `answer` on "Which runtime guardrail mechanisms (e.g., content filtering, schema validation) will you enable?": 300
- Batch `13:34:05` `preview` on "OpenAI moderation API + JSON schema checks add ~10 ms latency per request while blocking unsafe content.": 10
- Batch `13:34:05` `detail_hint` on "OpenAI moderation API + JSON schema checks add ~10 ms latency per request while blocking unsafe content.": 10
- Batch `13:34:05` `answer` on "OpenAI moderation API + JSON schema checks add ~10 ms latency per request while blocking unsafe content.": 10, 10, 180, 190, 4.3, 10
- Batch `13:34:05` `answer` on "Guardrails are runtime safeguards that sanitize both incoming prompts and outgoing responses.": 10, 4, 200
