import json
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("tenacity")

from jiri import analyzer
from jiri.config import AnalysisConfig, Config, default_config_text
from jiri.service import analyze_all, review_period, status


def _record(analysis: dict | None = None) -> dict:
    return {
        "archived_filename": "2026-08-27-001.mp4",
        "capture_time": "2026-08-27T20:00:00+08:00",
        "duration_seconds": 60,
        "transcription": {
            "status": "completed",
            "segments": [{"start_seconds": 0, "end_seconds": 4, "text": "今天完成了第一章，并计划明天整理笔记。"}],
        },
        "analysis": analysis or {},
    }


def _config(tmp_path: Path) -> Config:
    return Config(inbox=tmp_path / "inbox", archive=tmp_path, analysis=AnalysisConfig(enabled=True, model="test-model"))


def test_analyze_all_writes_result_and_skips_completed(tmp_path: Path, monkeypatch) -> None:
    metadata = tmp_path / "2026-08-27-001.json"
    metadata.write_text(json.dumps(_record()), encoding="utf-8")
    expected = {"status": "completed", "summary": "完成了第一章", "confidence": "high"}
    monkeypatch.setattr(analyzer, "analyze_daily_record", lambda record, config: expected)

    assert analyze_all(_config(tmp_path), date_from=date(2026, 8, 27)) == {"found": 1, "completed": 1, "failed": 0, "skipped": 0}
    assert json.loads(metadata.read_text(encoding="utf-8"))["analysis"] == expected
    assert analyze_all(_config(tmp_path)) == {"found": 1, "completed": 0, "failed": 0, "skipped": 1}


def test_review_writes_hidden_summary_that_status_ignores(tmp_path: Path, monkeypatch) -> None:
    metadata = tmp_path / "2026-08-27-001.json"
    metadata.write_text(json.dumps(_record({"status": "completed", "summary": "第一天"})), encoding="utf-8")
    monkeypatch.setattr(analyzer, "analyze_period", lambda records, config, start, end: {"status": "completed", "overview": "保持推进"})

    output = review_period(_config(tmp_path), date(2026, 8, 27), date(2026, 8, 27))
    assert json.loads(output.read_text(encoding="utf-8"))["overview"] == "保持推进"
    assert status(_config(tmp_path))["videos"] == 1


def test_daily_analysis_validates_json_response(monkeypatch) -> None:
    response = type("Response", (), {"choices": [type("Choice", (), {"message": type("Message", (), {"content": json.dumps({
        "summary": "完成第一章", "completed_items": ["第一章"], "planned_items": ["整理笔记"], "blockers": [], "highlights": ["有明确计划"],
        "improvements": [{"priority": 1, "issue": "缺少产出细节", "evidence": {"timestamp_seconds": 0, "quote": "完成了第一章"}, "action": "说明笔记数量"}],
        "tomorrow_focus": ["整理笔记"], "confidence": "high"
    })})})]})
    client = type("Client", (), {"chat": type("Chat", (), {"completions": type("Completions", (), {"create": lambda self, **kwargs: response})()})()})()
    monkeypatch.setattr(analyzer, "_client", lambda config: client)

    result = analyzer.analyze_daily_record(_record(), AnalysisConfig(enabled=True, model="test-model"))
    assert result["status"] == "completed"
    assert result["improvements"][0]["evidence"]["quote"] == "完成了第一章"


def test_default_config_includes_analysis_section(tmp_path: Path) -> None:
    text = default_config_text(tmp_path / "inbox", tmp_path / "archive")
    assert "[analysis]" in text
    assert 'api_key_env = "JIRI_ANALYSIS_API_KEY"' in text
