"""项目中的轻量数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Transcription:
    """保存转写状态和带时间戳的文本片段。"""

    status: str = "pending"
    model: str | None = None
    language: str | None = None
    error: str | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VideoRecord:
    """一个视频及其旁车 JSON 的完整记录。"""

    schema_version: int
    original_filename: str
    archived_filename: str
    capture_time: str
    capture_time_source: str
    duration_seconds: float | None
    width: int | None
    height: int | None
    format: str
    sha256: str
    source_path: str
    transcription: Transcription = field(default_factory=Transcription)
    analysis: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为稳定的 JSON 结构。"""

        return asdict(self)


def isoformat(value: datetime) -> str:
    """统一生成带时区的 ISO 8601 时间。"""

    return value.astimezone().isoformat(timespec="seconds")
