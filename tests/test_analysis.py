import json
import tomllib
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("tenacity")

from jiri import analyzer
from jiri import cli
from jiri.config import AnalysisConfig, Config, default_config_text
from jiri.service import available_analysis_dates, analyze_all, render_daily_analyses, review_period, show_daily_analyses, status


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


def test_analyze_all_writes_result_and_skips_completed(tmp_path: Path, monkeypatch, capsys) -> None:
    metadata = tmp_path / "2026-08-27-001.json"
    metadata.write_text(json.dumps(_record()), encoding="utf-8")
    expected = {"status": "completed", "summary": "完成了第一章", "confidence": "high"}
    monkeypatch.setattr(analyzer, "validate_analysis_config", lambda config: None)
    monkeypatch.setattr(analyzer, "analyze_daily_record", lambda record, config: expected)

    assert analyze_all(_config(tmp_path), date_from=date(2026, 8, 27)) == {"found": 1, "completed": 1, "failed": 0, "skipped": 0}
    assert json.loads(metadata.read_text(encoding="utf-8"))["analysis"] == expected
    output = capsys.readouterr().out
    assert "[1/1] 正在分析" in output
    assert "完成：完成了第一章" in output
    assert analyze_all(_config(tmp_path)) == {"found": 1, "completed": 0, "failed": 0, "skipped": 1}


def test_analyze_all_preserves_records_when_startup_configuration_is_invalid(tmp_path: Path, monkeypatch) -> None:
    metadata = tmp_path / "2026-08-27-001.json"
    original = _record()
    metadata.write_text(json.dumps(original), encoding="utf-8")

    def fail_validation(config) -> None:
        raise RuntimeError("未设置环境变量 DEEPSEEK_API_KEY")

    monkeypatch.setattr(analyzer, "validate_analysis_config", fail_validation, raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        analyze_all(_config(tmp_path))
    assert json.loads(metadata.read_text(encoding="utf-8")) == original


def test_review_writes_hidden_summary_that_status_ignores(tmp_path: Path, monkeypatch) -> None:
    metadata = tmp_path / "2026-08-27-001.json"
    metadata.write_text(json.dumps(_record({"status": "completed", "summary": "第一天"})), encoding="utf-8")
    monkeypatch.setattr(analyzer, "validate_analysis_config", lambda config: None)
    monkeypatch.setattr(analyzer, "analyze_period", lambda records, config, start, end: {"status": "completed", "overview": "保持推进"})

    output = review_period(_config(tmp_path), date(2026, 8, 27), date(2026, 8, 27))
    assert json.loads(output.read_text(encoding="utf-8"))["overview"] == "保持推进"
    assert status(_config(tmp_path))["videos"] == 1


def test_show_daily_analyses_reads_saved_result_without_calling_ai(tmp_path: Path, capsys) -> None:
    metadata = tmp_path / "2026-08-27-001.json"
    metadata.write_text(json.dumps(_record({"status": "completed", "summary": "第一天", "highlights": ["持续记录"], "improvements": [], "tomorrow_focus": []})), encoding="utf-8")

    assert show_daily_analyses(_config(tmp_path), date(2026, 8, 27)) == 1
    output = capsys.readouterr().out
    assert "2026-08-27 的分析结果" in output
    assert "完成：第一天" in output


def test_render_daily_analyses_returns_screen_safe_text(tmp_path: Path) -> None:
    metadata = tmp_path / "2026-08-27-001.json"
    metadata.write_text(json.dumps(_record({"status": "completed", "summary": "第一天", "highlights": [], "dimension_assessments": [], "improvements": [], "tomorrow_focus": []})), encoding="utf-8")

    rendered = render_daily_analyses(_config(tmp_path), date(2026, 8, 27))
    assert "2026-08-27 的分析结果（1 条）" in rendered
    assert "完成：第一天" in rendered


def test_compact_daily_rendering_omits_long_evidence_and_actions(tmp_path: Path) -> None:
    metadata = tmp_path / "2026-08-27-001.json"
    analysis = {
        "status": "completed", "summary": "第一天", "highlights": ["持续记录"], "dimension_assessments": [], "tomorrow_focus": ["明日任务"],
        "improvements": [{"priority": 1, "issue": "睡眠不足", "evidence": {"timestamp_seconds": 12, "quote": "非常长的原话证据"}, "action": "非常长的行动方案"}],
    }
    metadata.write_text(json.dumps(_record(analysis)), encoding="utf-8")

    rendered = render_daily_analyses(_config(tmp_path), date(2026, 8, 27), compact=True)
    assert "睡眠不足" in rendered
    assert "非常长的原话证据" not in rendered
    assert "非常长的行动方案" not in rendered


def test_available_analysis_dates_returns_only_completed_days(tmp_path: Path) -> None:
    first = _record({"status": "completed", "summary": "第一天"})
    second = _record({"status": "pending"})
    second["capture_time"] = "2026-08-28T20:00:00+08:00"
    (tmp_path / "first.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "second.json").write_text(json.dumps(second), encoding="utf-8")

    assert available_analysis_dates(_config(tmp_path)) == [date(2026, 8, 27)]


def test_browser_navigation_uses_single_keys_and_stays_in_bounds() -> None:
    assert cli._move_browser_index(2, "left", 3) == 1
    assert cli._move_browser_index(1, "right", 3) == 2
    assert cli._move_browser_index(0, "left", 3) == 0
    assert cli._move_browser_index(2, "right", 3) == 2
    assert cli._move_browser_index(1, "x", 3) == 1


def test_browser_supports_arrow_navigation_and_advertises_it() -> None:
    assert cli._move_browser_index(2, "left", 3) == 1
    assert cli._move_browser_index(1, "right", 3) == 2
    assert "←" in cli.BROWSE_HINT
    assert "→" in cli.BROWSE_HINT


def test_browser_scrolls_with_up_and_down_and_wraps_chinese_text() -> None:
    assert cli._move_browser_scroll(1, "up", 3) == 0
    assert cli._move_browser_scroll(2, "down", 3) == 3
    assert cli._move_browser_scroll(0, "up", 3) == 0
    assert cli._move_browser_scroll(3, "down", 3) == 3
    assert cli._wrap_browser_content("中文测试", 4) == ["中文", "测试"]


def test_daily_analysis_validates_json_response(monkeypatch) -> None:
    dimensions = [
        {"id": item.id, "label": item.label, "assessment": "有记录", "evidence": {"timestamp_seconds": 0, "quote": "完成了第一章"}, "next_action": None}
        for item in AnalysisConfig().dimensions
    ]
    response = type("Response", (), {"choices": [type("Choice", (), {"message": type("Message", (), {"content": json.dumps({
        "summary": "完成第一章", "completed_items": ["第一章"], "planned_items": ["整理笔记"], "blockers": [], "highlights": ["有明确计划"],
        "dimension_assessments": dimensions,
        "improvements": [{"priority": 1, "issue": "缺少产出细节", "evidence": {"timestamp_seconds": 0, "quote": "完成了第一章"}, "action": "说明笔记数量"}],
        "tomorrow_focus": ["整理笔记"], "confidence": "high"
    })})})]})
    requests = []

    def create(self, **kwargs):
        requests.append(kwargs)
        return response

    client = type("Client", (), {"chat": type("Chat", (), {"completions": type("Completions", (), {"create": create})()})()})()
    monkeypatch.setattr(analyzer, "_client", lambda config: client)

    result = analyzer.analyze_daily_record(
        _record(),
        AnalysisConfig(
            enabled=True,
            model="test-model",
            daily_prompt="这是 TOML 内联的日分析提示词，要求更加关注学习方法。",
        ),
    )
    assert result["status"] == "completed"
    assert result["improvements"][0]["evidence"]["quote"] == "完成了第一章"
    assert result["prompt_source"] == "built-in + config.toml"
    system_prompt = requests[0]["messages"][0]["content"]
    assert "日课视频复盘教练" in system_prompt
    assert "TOML 内联的日分析提示词" in system_prompt


def test_daily_analysis_retries_empty_api_content(monkeypatch) -> None:
    dimensions = [
        {"id": item.id, "label": item.label, "assessment": "有记录", "evidence": {"timestamp_seconds": 0, "quote": "完成了第一章"}, "next_action": None}
        for item in AnalysisConfig().dimensions
    ]
    valid_content = json.dumps({
        "summary": "完成第一章", "completed_items": ["第一章"], "planned_items": [], "blockers": [], "highlights": [],
        "dimension_assessments": dimensions, "improvements": [], "tomorrow_focus": [], "confidence": "high",
    })
    calls = []

    def create(self, **kwargs):
        calls.append(kwargs)
        content = None if len(calls) == 1 else valid_content
        return type("Response", (), {"choices": [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]})()

    client = type("Client", (), {"chat": type("Chat", (), {"completions": type("Completions", (), {"create": create})()})()})()
    monkeypatch.setattr(analyzer, "_client", lambda config: client)

    result = analyzer.analyze_daily_record(_record(), AnalysisConfig(enabled=True, model="test-model"))
    assert result["status"] == "completed"
    assert len(calls) == 2


def test_daily_analysis_retries_truncated_json(monkeypatch) -> None:
    dimensions = [
        {"id": item.id, "label": item.label, "assessment": "有记录", "evidence": {"timestamp_seconds": 0, "quote": "完成了第一章"}, "next_action": None}
        for item in AnalysisConfig().dimensions
    ]
    valid_content = json.dumps({
        "summary": "完成第一章", "completed_items": ["第一章"], "planned_items": [], "blockers": [], "highlights": [],
        "dimension_assessments": dimensions, "improvements": [], "tomorrow_focus": [], "confidence": "high",
    })
    calls = []

    def create(self, **kwargs):
        calls.append(kwargs)
        content = '{"summary": "未结束' if len(calls) == 1 else valid_content
        return type("Response", (), {"choices": [type("Choice", (), {"message": type("Message", (), {"content": content})()})()]})()

    client = type("Client", (), {"chat": type("Chat", (), {"completions": type("Completions", (), {"create": create})()})()})()
    monkeypatch.setattr(analyzer, "_client", lambda config: client)

    result = analyzer.analyze_daily_record(_record(), AnalysisConfig(enabled=True, model="test-model"))
    assert result["status"] == "completed"
    assert len(calls) == 2


def test_default_config_includes_analysis_section(tmp_path: Path) -> None:
    text = default_config_text(tmp_path / "inbox", tmp_path / "archive")
    assert "[analysis]" in text
    assert 'api_key_env = "JIRI_ANALYSIS_API_KEY"' in text
    parsed = tomllib.loads(text)
    assert "目标清晰度" in parsed["analysis"]["daily_prompt"]
    assert "持续进步" in parsed["analysis"]["review_prompt"]


def test_setup_creates_sample_config_when_missing(tmp_path: Path, monkeypatch) -> None:
    sample_config = tmp_path / "config" / "jiri.toml"
    monkeypatch.setattr(cli, "DEFAULT_INBOX", tmp_path / "inbox")
    monkeypatch.setattr(cli, "DEFAULT_ARCHIVE", tmp_path / "archive")

    cli._create_sample_config_if_missing(sample_config)

    parsed = tomllib.loads(sample_config.read_text(encoding="utf-8"))
    assert parsed["paths"]["archive"] == str(tmp_path / "archive")
    assert "目标清晰度" in parsed["analysis"]["daily_prompt"]
