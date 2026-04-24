from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MEETING_EXPORTS_DIR = ROOT / "eval" / "fixtures" / "meeting_exports"
TWINMIND_TXT_DIR = ROOT / "eval" / "fixtures" / "twinmind_txt"
TWINMIND_BENCHMARK_FILE = ROOT / "eval" / "fixtures" / "twinmind_benchmark" / "twinmind_output.json"


def load_export_script():
    script_path = ROOT / "scripts" / "export_eval_cases.py"
    spec = importlib.util.spec_from_file_location("export_eval_cases", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_benchmark_script():
    script_path = ROOT / "scripts" / "evaluate_benchmark.py"
    spec = importlib.util.spec_from_file_location("evaluate_benchmark", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def meeting_export_files() -> list[Path]:
    return sorted(MEETING_EXPORTS_DIR.glob("meeting-*.json"))


def twinmind_text_files() -> list[Path]:
    return sorted(TWINMIND_TXT_DIR.glob("*.txt"))


def test_fixture_directories_exist() -> None:
    assert MEETING_EXPORTS_DIR.exists()
    assert TWINMIND_TXT_DIR.exists()


def test_meeting_exports_generate_eval_cases(tmp_path: Path) -> None:
    exports = meeting_export_files()
    if not exports:
        pytest.skip("Add meeting-*.json files to eval/fixtures/meeting_exports to enable this test.")

    module = load_export_script()
    all_cases = []
    for export in exports:
        cases = module.build_cases_from_file(export, context_lines=24)
        assert cases, f"No cases were built from {export.name}"
        all_cases.extend(cases)

    jsonl_path = tmp_path / "eval_cases.jsonl"
    md_path = tmp_path / "twinmind_eval_packet.md"
    module.write_jsonl(all_cases, jsonl_path)
    module.write_markdown_packet(all_cases, md_path)

    lines = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert lines
    assert all("case_id" in line for line in lines)
    assert md_path.read_text(encoding="utf-8").startswith("# TwinMind Manual Eval Packet")


def test_twinmind_text_fixtures_are_present_and_non_empty() -> None:
    txt_files = twinmind_text_files()
    if not txt_files:
        pytest.skip("Add TwinMind .txt files to eval/fixtures/twinmind_txt to enable this test.")

    for txt_file in txt_files:
        content = txt_file.read_text(encoding="utf-8").strip()
        assert content, f"{txt_file.name} is empty"


def test_benchmark_export_can_be_compared() -> None:
    exports = meeting_export_files()
    if not exports or not TWINMIND_BENCHMARK_FILE.exists():
        pytest.skip("Add both a meeting export and eval/fixtures/twinmind_benchmark/twinmind_output.json")

    module = load_benchmark_script()
    report = module.compare_exports(  # type: ignore[attr-defined]
        module.load_json(exports[0]),  # type: ignore[attr-defined]
        module.load_json(TWINMIND_BENCHMARK_FILE),  # type: ignore[attr-defined]
    )

    assert report["batch_pairs_evaluated"] >= 1
    assert 0 <= report["summary"]["overall_score_100"] <= 100
