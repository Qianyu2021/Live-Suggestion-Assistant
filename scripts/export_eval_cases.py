#!/usr/bin/env python3
"""
Build TwinMind manual-eval packets from exported meeting JSON files.

Input format matches frontend export (meeting-*.json):
{
  "transcript": [{"ts":"HH:MM:SS","text":"..."}],
  "suggestionBatches": [{"ts":"HH:MM:SS","suggestions":[...]}],
  "chatHistory": [{"ts":"HH:MM:SS","role":"user|assistant","content":"..."}]
}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_hms(ts: str) -> int:
    try:
        h, m, s = ts.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return -1


def transcript_until_batch(transcript: list[dict[str, Any]], batch_ts: str) -> list[dict[str, Any]]:
    cutoff = parse_hms(batch_ts)
    if cutoff < 0:
        return transcript
    filtered = [line for line in transcript if parse_hms(str(line.get("ts", ""))) <= cutoff]
    return filtered or transcript


def find_clicked_answer(chat_history: list[dict[str, Any]], preview: str) -> str:
    preview_norm = preview.strip()
    for i, msg in enumerate(chat_history):
        if str(msg.get("role", "")).lower() != "user":
            continue
        if str(msg.get("content", "")).strip() != preview_norm:
            continue
        for j in range(i + 1, len(chat_history)):
            nxt = chat_history[j]
            if str(nxt.get("role", "")).lower() == "assistant":
                return str(nxt.get("content", "")).strip()
    return ""


def build_cases_from_file(path: Path, context_lines: int) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    transcript = data.get("transcript", []) or []
    batches = data.get("suggestionBatches", []) or []
    chat_history = data.get("chatHistory", []) or []

    # Export format stores newest-first. Reverse for chronological evaluation.
    chrono_batches = list(reversed(batches))
    cases: list[dict[str, Any]] = []
    case_counter = 1

    for b_idx, batch in enumerate(chrono_batches, start=1):
        b_ts = str(batch.get("ts", ""))
        suggestions = batch.get("suggestions", []) or []
        seen_upto = transcript_until_batch(transcript, b_ts)
        recent = seen_upto[-context_lines:]
        recent_text = "\n".join(f"{l.get('ts', '')} {l.get('text', '')}".strip() for l in recent)

        for s_idx, s in enumerate(suggestions, start=1):
            preview = str(s.get("preview", "")).strip()
            detail_hint = str(s.get("detail_hint", "")).strip()
            s_type = str(s.get("type", "")).strip().upper()

            case = {
                "case_id": f"{path.stem}-c{case_counter:03d}",
                "source_file": path.name,
                "batch_index": b_idx,
                "batch_ts": b_ts,
                "card_index": s_idx,
                "card_type": s_type,
                "card_preview": preview,
                "card_detail_hint": detail_hint,
                "transcript_recent_context": recent_text,
                "clicked_assistant_answer": find_clicked_answer(chat_history, preview),
            }
            cases.append(case)
            case_counter += 1

    return cases


def write_jsonl(cases: list[dict[str, Any]], out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def write_markdown_packet(cases: list[dict[str, Any]], out_path: Path) -> None:
    lines: list[str] = []
    lines.append("# TwinMind Manual Eval Packet")
    lines.append("")
    lines.append("Use each case below with TwinMind and score using `eval/TWINMIND_EVAL_TEMPLATE.md`.")
    lines.append("")

    for c in cases:
        lines.append(f"## {c['case_id']}")
        lines.append(f"- Source: `{c['source_file']}`")
        lines.append(f"- Batch: `{c['batch_index']}` at `{c['batch_ts']}`")
        lines.append(f"- Card: `{c['card_type']}`")
        lines.append("")
        lines.append("### Transcript Context")
        lines.append("```text")
        lines.append(c["transcript_recent_context"] or "(empty)")
        lines.append("```")
        lines.append("")
        lines.append("### Card Preview")
        lines.append(c["card_preview"] or "(empty)")
        lines.append("")
        lines.append("### Card Detail Hint")
        lines.append(c["card_detail_hint"] or "(empty)")
        lines.append("")
        lines.append("### Clicked Detailed Answer")
        lines.append("```text")
        lines.append(c["clicked_assistant_answer"] or "(no clicked answer found)")
        lines.append("```")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export eval cases for TwinMind manual judging.")
    parser.add_argument(
        "inputs",
        nargs="+",
        help="One or more meeting export JSON files (e.g. meeting-2026-04-23T10-00-00.json)",
    )
    parser.add_argument("--out-dir", default="eval/out", help="Output directory (default: eval/out)")
    parser.add_argument("--context-lines", type=int, default=24, help="Transcript lines per case context")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_cases: list[dict[str, Any]] = []
    for in_path in args.inputs:
        p = Path(in_path)
        if not p.exists():
            print(f"Skipping missing file: {p}")
            continue
        all_cases.extend(build_cases_from_file(p, args.context_lines))

    if not all_cases:
        raise SystemExit("No cases found. Provide valid meeting export JSON files.")

    jsonl_path = out_dir / "eval_cases.jsonl"
    md_path = out_dir / "twinmind_eval_packet.md"
    write_jsonl(all_cases, jsonl_path)
    write_markdown_packet(all_cases, md_path)

    print(f"Wrote {len(all_cases)} cases")
    print(f"- {jsonl_path}")
    print(f"- {md_path}")


if __name__ == "__main__":
    main()

