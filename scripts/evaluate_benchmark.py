#!/usr/bin/env python3
"""
Compare one meeting export against a TwinMind benchmark export.

This is a lightweight offline benchmark:
- aligns suggestion batches chronologically
- matches cards within each batch for best semantic fit
- scores suggestion quality and clicked-answer similarity
- flags unsupported numeric claims that appear in the candidate but not the
  transcript or benchmark
"""

from __future__ import annotations

import argparse
import difflib
import itertools
import json
import re
from pathlib import Path
from typing import Any


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "both", "but", "by", "for", "from",
    "how", "if", "in", "into", "is", "it", "its", "of", "on", "or", "so", "that",
    "the", "their", "them", "they", "this", "to", "use", "what", "which", "will",
    "with", "you", "your",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_hms(ts: str) -> int:
    try:
        h, m, s = ts.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return -1


def chrono_batches(export: dict[str, Any]) -> list[dict[str, Any]]:
    batches = export.get("suggestionBatches", []) or []
    return sorted(batches, key=lambda b: parse_hms(str(b.get("ts", ""))))


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


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def content_tokens(text: str) -> set[str]:
    return {tok for tok in tokenize(text) if tok not in STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def normalized_text_similarity(a: str, b: str) -> float:
    a_tokens = content_tokens(a)
    b_tokens = content_tokens(b)
    token_score = jaccard(a_tokens, b_tokens)
    seq_score = difflib.SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()
    base = 0.6 * token_score + 0.4 * seq_score
    if not a.strip() and not b.strip():
        return 1.0
    prefix_bonus = 0.0
    if a.strip() and b.strip():
        shorter = min(len(a.strip()), len(b.strip()))
        if shorter:
            shared_prefix = 0
            for ca, cb in zip(a.strip().lower(), b.strip().lower()):
                if ca != cb:
                    break
                shared_prefix += 1
            prefix_bonus = min(shared_prefix / shorter, 0.2)
    return min(base + prefix_bonus, 1.0)


def standalone_preview_penalty(preview: str) -> float:
    p = " ".join(preview.strip().lower().split())
    if not p:
        return 1.0
    teaser_prefixes = (
        "ask about ", "ask whether ", "discuss ", "look into ", "explore ",
        "clarify ", "follow up on ", "dig into ",
    )
    penalty = 0.0
    if any(p.startswith(prefix) for prefix in teaser_prefixes):
        penalty += 0.45
    if len(content_tokens(preview)) < 5:
        penalty += 0.25
    return min(penalty, 1.0)


def unsupported_numeric_claims(text: str, transcript_text: str, benchmark_text: str) -> list[str]:
    nums = re.findall(r"\b\d+(?:\.\d+)?%?|\b~\d+(?:\.\d+)?%?", text)
    if not nums:
        return []
    transcript_nums = set(re.findall(r"\b\d+(?:\.\d+)?%?|\b~\d+(?:\.\d+)?%?", transcript_text))
    benchmark_nums = set(re.findall(r"\b\d+(?:\.\d+)?%?|\b~\d+(?:\.\d+)?%?", benchmark_text))
    allowed = transcript_nums | benchmark_nums
    return [n for n in nums if n not in allowed]


def card_alignment_score(candidate: dict[str, Any], benchmark: dict[str, Any]) -> float:
    preview_score = normalized_text_similarity(
        str(candidate.get("preview", "")),
        str(benchmark.get("preview", "")),
    )
    detail_score = normalized_text_similarity(
        str(candidate.get("detail_hint", "")),
        str(benchmark.get("detail_hint", "")),
    )
    score = 0.65 * preview_score + 0.35 * detail_score
    if str(candidate.get("type", "")).upper() == str(benchmark.get("type", "")).upper():
        score += 0.1
    return score


def best_card_matches(candidate_cards: list[dict[str, Any]], benchmark_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidate_cards or not benchmark_cards:
        return []

    n = min(len(candidate_cards), len(benchmark_cards))
    best_perm: tuple[int, ...] | None = None
    best_score = -1.0
    for perm in itertools.permutations(range(len(benchmark_cards)), n):
        total = 0.0
        for idx in range(n):
            total += card_alignment_score(candidate_cards[idx], benchmark_cards[perm[idx]])
        if total > best_score:
            best_score = total
            best_perm = perm

    assert best_perm is not None
    matches: list[dict[str, Any]] = []
    for idx in range(n):
        c = candidate_cards[idx]
        b = benchmark_cards[best_perm[idx]]
        matches.append(
            {
                "candidate": c,
                "benchmark": b,
                "preview_score": normalized_text_similarity(str(c.get("preview", "")), str(b.get("preview", ""))),
                "detail_score": normalized_text_similarity(str(c.get("detail_hint", "")), str(b.get("detail_hint", ""))),
                "answer_score": 0.0,
                "type_match": str(c.get("type", "")).upper() == str(b.get("type", "")).upper(),
            }
        )
    return matches


def compare_exports(candidate: dict[str, Any], benchmark: dict[str, Any]) -> dict[str, Any]:
    candidate_batches = chrono_batches(candidate)
    benchmark_batches = chrono_batches(benchmark)
    pair_count = min(len(candidate_batches), len(benchmark_batches))
    transcript_text = "\n".join(str(t.get("text", "")) for t in candidate.get("transcript", []) or [])
    benchmark_text = json.dumps(benchmark, ensure_ascii=False)
    candidate_chat = candidate.get("chatHistory", []) or []
    benchmark_chat = benchmark.get("chatHistory", []) or []

    batch_reports: list[dict[str, Any]] = []
    all_preview_scores: list[float] = []
    all_detail_scores: list[float] = []
    all_answer_scores: list[float] = []
    type_match_count = 0
    unsupported_numbers: list[dict[str, Any]] = []
    standalone_penalties: list[float] = []

    for idx in range(pair_count):
        candidate_batch = candidate_batches[idx]
        benchmark_batch = benchmark_batches[idx]
        matches = best_card_matches(
            list(candidate_batch.get("suggestions", []) or []),
            list(benchmark_batch.get("suggestions", []) or []),
        )
        match_reports: list[dict[str, Any]] = []
        for match in matches:
            cand_preview = str(match["candidate"].get("preview", ""))
            bench_preview = str(match["benchmark"].get("preview", ""))
            cand_answer = find_clicked_answer(candidate_chat, cand_preview)
            bench_answer = find_clicked_answer(benchmark_chat, bench_preview)
            answer_score = normalized_text_similarity(cand_answer, bench_answer)
            match["answer_score"] = answer_score

            all_preview_scores.append(match["preview_score"])
            all_detail_scores.append(match["detail_score"])
            if cand_answer and bench_answer:
                all_answer_scores.append(answer_score)
            if match["type_match"]:
                type_match_count += 1

            penalty = standalone_preview_penalty(cand_preview)
            standalone_penalties.append(penalty)

            for field_name, text in (
                ("preview", cand_preview),
                ("detail_hint", str(match["candidate"].get("detail_hint", ""))),
                ("answer", cand_answer),
            ):
                unsupported = unsupported_numeric_claims(text, transcript_text, benchmark_text)
                if unsupported:
                    unsupported_numbers.append(
                        {
                            "batch_ts": candidate_batch.get("ts", ""),
                            "preview": cand_preview,
                            "field": field_name,
                            "values": unsupported,
                        }
                    )

            match_reports.append(
                {
                    "candidate_type": match["candidate"].get("type", ""),
                    "benchmark_type": match["benchmark"].get("type", ""),
                    "candidate_preview": cand_preview,
                    "benchmark_preview": bench_preview,
                    "preview_score": round(match["preview_score"], 3),
                    "detail_score": round(match["detail_score"], 3),
                    "answer_score": round(answer_score, 3) if cand_answer and bench_answer else None,
                    "standalone_penalty": round(penalty, 3),
                }
            )

        batch_reports.append(
            {
                "candidate_batch_ts": candidate_batch.get("ts", ""),
                "benchmark_batch_ts": benchmark_batch.get("ts", ""),
                "matches": match_reports,
            }
        )

    card_count = max(sum(len(b.get("suggestions", []) or []) for b in candidate_batches[:pair_count]), 1)
    preview_mean = sum(all_preview_scores) / max(len(all_preview_scores), 1)
    detail_mean = sum(all_detail_scores) / max(len(all_detail_scores), 1)
    answer_mean = sum(all_answer_scores) / max(len(all_answer_scores), 1) if all_answer_scores else 0.0
    standalone_mean = 1.0 - (sum(standalone_penalties) / max(len(standalone_penalties), 1))
    type_match_rate = type_match_count / card_count
    unsupported_penalty = min(len(unsupported_numbers) * 0.04, 0.25)

    overall = (
        0.45 * preview_mean
        + 0.20 * detail_mean
        + 0.20 * answer_mean
        + 0.10 * standalone_mean
        + 0.05 * type_match_rate
        - unsupported_penalty
    )
    overall = max(0.0, min(overall, 1.0))

    return {
        "batch_pairs_evaluated": pair_count,
        "summary": {
            "overall_score_100": round(overall * 100, 1),
            "preview_similarity_100": round(preview_mean * 100, 1),
            "detail_similarity_100": round(detail_mean * 100, 1),
            "clicked_answer_similarity_100": round(answer_mean * 100, 1),
            "standalone_preview_score_100": round(standalone_mean * 100, 1),
            "type_match_rate_100": round(type_match_rate * 100, 1),
            "unsupported_numeric_claim_count": len(unsupported_numbers),
        },
        "batch_reports": batch_reports,
        "unsupported_numeric_claims": unsupported_numbers,
    }


def write_markdown(report: dict[str, Any], out_path: Path) -> None:
    summary = report["summary"]
    lines = [
        "# TwinMind Benchmark Report",
        "",
        f"- Batch pairs evaluated: `{report['batch_pairs_evaluated']}`",
        f"- Overall score: `{summary['overall_score_100']}` / 100",
        f"- Preview similarity: `{summary['preview_similarity_100']}` / 100",
        f"- Detail similarity: `{summary['detail_similarity_100']}` / 100",
        f"- Clicked-answer similarity: `{summary['clicked_answer_similarity_100']}` / 100",
        f"- Standalone preview score: `{summary['standalone_preview_score_100']}` / 100",
        f"- Type match rate: `{summary['type_match_rate_100']}` / 100",
        f"- Unsupported numeric claims: `{summary['unsupported_numeric_claim_count']}`",
        "",
        "## Batch Detail",
        "",
    ]

    for batch in report["batch_reports"]:
        lines.append(
            f"### Candidate `{batch['candidate_batch_ts']}` vs Benchmark `{batch['benchmark_batch_ts']}`"
        )
        lines.append("")
        for match in batch["matches"]:
            lines.append(
                f"- `{match['candidate_type']}` -> `{match['benchmark_type']}` | "
                f"preview `{match['preview_score']}` | detail `{match['detail_score']}` | "
                f"answer `{match['answer_score']}` | standalone `{1 - match['standalone_penalty']:.3f}`"
            )
            lines.append(f"  Candidate: {match['candidate_preview']}")
            lines.append(f"  Benchmark: {match['benchmark_preview']}")
        lines.append("")

    if report["unsupported_numeric_claims"]:
        lines.append("## Unsupported Numeric Claims")
        lines.append("")
        for item in report["unsupported_numeric_claims"]:
            lines.append(
                f"- Batch `{item['batch_ts']}` `{item['field']}` on \"{item['preview']}\": {', '.join(item['values'])}"
            )
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate one export against a TwinMind benchmark export.")
    parser.add_argument("candidate", help="Candidate meeting export JSON")
    parser.add_argument("benchmark", help="TwinMind benchmark export JSON")
    parser.add_argument("--out-dir", default="eval/out", help="Output directory")
    args = parser.parse_args()

    candidate_path = Path(args.candidate)
    benchmark_path = Path(args.benchmark)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = compare_exports(load_json(candidate_path), load_json(benchmark_path))

    json_path = out_dir / "benchmark_report.json"
    md_path = out_dir / "benchmark_report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(report, md_path)

    print(f"Overall score: {report['summary']['overall_score_100']}/100")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


if __name__ == "__main__":
    main()
