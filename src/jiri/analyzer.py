"""通过 OpenAI-compatible API 对已转写的日课视频做结构化复盘。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import AnalysisConfig


PROMPT_VERSION = "daily-review-v1"
REVIEW_PROMPT_VERSION = "weekly-review-v1"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp_seconds: float | None = Field(default=None, ge=0)
    quote: str | None = None


class Improvement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: int = Field(ge=1, le=3)
    issue: str
    evidence: Evidence
    action: str


class DailyReview(BaseModel):
    """持久化到视频 JSON 的、可被程序安全读取的模型输出。"""

    model_config = ConfigDict(extra="forbid")
    summary: str
    completed_items: list[str]
    planned_items: list[str]
    blockers: list[str]
    highlights: list[str]
    improvements: list[Improvement] = Field(max_length=3)
    tomorrow_focus: list[str] = Field(max_length=3)
    confidence: Literal["high", "medium", "low"]


class PeriodReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overview: str
    progress: list[str] = Field(max_length=5)
    recurring_patterns: list[str] = Field(max_length=5)
    focus_next_period: list[str] = Field(max_length=3)
    confidence: Literal["high", "medium", "low"]


DAILY_SYSTEM_PROMPT = """你是日课视频复盘教练。仅依据输入的转写文本和元数据分析，不得猜测未提及的事实。
区分计划、行动、结果、阻碍和反思。改进建议最多三条，按影响力排序；每条必须引用转写原话或时间戳，若无法引用则在 evidence 的两个字段填 null。
建议必须是下一次日课中可执行的具体动作。避免人格评判、心理诊断和空泛鼓励。只返回符合给定 JSON Schema 的 JSON。"""

PERIOD_SYSTEM_PROMPT = """你是日课长期复盘教练。仅使用输入的每日分析，不得补充未出现的事实。
指出可观察到的进步和重复模式，并给出最多三项下一周期可执行的重点。避免人格评判、心理诊断和空泛鼓励。只返回符合给定 JSON Schema 的 JSON。"""


def analyze_daily_record(record: dict[str, Any], config: AnalysisConfig) -> dict[str, Any]:
    """调用模型并以分析元数据包装单条日课复盘。"""

    transcription = record.get("transcription", {})
    segments = transcription.get("segments", [])
    if transcription.get("status") != "completed" or not segments:
        raise ValueError("视频尚无可用转写结果")
    payload = {
        "capture_time": record.get("capture_time"),
        "duration_seconds": record.get("duration_seconds"),
        "transcript": [
            {"start_seconds": item.get("start_seconds"), "end_seconds": item.get("end_seconds"), "text": item.get("text", "")}
            for item in segments
        ],
    }
    result = _request_json(config, DAILY_SYSTEM_PROMPT, payload, DailyReview)
    return {
        "status": "completed",
        "model": config.model,
        "prompt_version": PROMPT_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        **result.model_dump(),
    }


def analyze_period(records: list[dict[str, Any]], config: AnalysisConfig, date_from: str, date_to: str) -> dict[str, Any]:
    """从已完成的单日分析生成一个周/月趋势回顾。"""

    payload = {
        "period": {"from": date_from, "to": date_to},
        "daily_reviews": [
            {"capture_time": record.get("capture_time"), "analysis": _daily_analysis_fields(record.get("analysis", {}))}
            for record in records
        ],
    }
    result = _request_json(config, PERIOD_SYSTEM_PROMPT, payload, PeriodReview)
    return {
        "status": "completed",
        "model": config.model,
        "prompt_version": REVIEW_PROMPT_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "period": payload["period"],
        "source_videos": len(records),
        **result.model_dump(),
    }


def _daily_analysis_fields(analysis: dict[str, Any]) -> dict[str, Any]:
    keys = ("summary", "completed_items", "planned_items", "blockers", "highlights", "improvements", "tomorrow_focus", "confidence")
    return {key: analysis.get(key) for key in keys}


def _client(config: AnalysisConfig):
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("未安装分析依赖，请先运行 jiri setup --analysis") from error
    if not config.enabled:
        raise RuntimeError("分析尚未启用，请在配置的 [analysis] 中设置 enabled = true")
    if not config.model:
        raise RuntimeError("未设置 [analysis].model")
    api_key = os.getenv(config.api_key_env)
    if not api_key:
        raise RuntimeError(f"未设置环境变量 {config.api_key_env}")
    return OpenAI(api_key=api_key, base_url=config.api_base or None, timeout=config.timeout_seconds)


def _request_json(config: AnalysisConfig, system_prompt: str, payload: dict[str, Any], schema: type[BaseModel]) -> BaseModel:
    """使用 Chat Completions JSON 模式，兼容 OpenAI-compatible 服务。"""

    client = _client(config)
    schema_prompt = (
        f"{system_prompt}\n\n必须符合如下 JSON Schema："
        f"\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )

    @retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        wait=wait_exponential(min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def create_completion():
        return client.chat.completions.create(
            model=config.model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": schema_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )

    response = create_completion()
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("分析 API 返回了空内容")
    try:
        return schema.model_validate_json(content)
    except Exception as error:
        raise RuntimeError(f"分析 API 返回的 JSON 不符合约定：{error}") from error
