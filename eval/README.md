# Manual TwinMind Eval Workflow

This project uses TwinMind as a **manual judge** (offline evaluation), not as a runtime API dependency.

## 1) Export Session Data

From the app UI, export one or more session files:
- `meeting-YYYY-MM-DDTHH-MM-SS.json`

## 2) Build Eval Cases

From project root:

```bash
python3 scripts/export_eval_cases.py meeting-*.json --out-dir eval/out --context-lines 24
```

Outputs:
- `eval/out/eval_cases.jsonl`
- `eval/out/twinmind_eval_packet.md`

## 3) Judge in TwinMind

For each case in `eval/out/twinmind_eval_packet.md`:
1. Paste case content into TwinMind
2. Score using `eval/TWINMIND_EVAL_TEMPLATE.md`
3. Record scores and notes

## 4) Compare Prompt/Code Versions

Run this workflow for each experiment branch/version and compare:
- mean weighted score
- latency scores
- recurring failure modes

