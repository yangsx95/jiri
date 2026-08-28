"""读取和保存 jiri 配置。"""

from __future__ import annotations

import os
import platform
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "jiri" / "config.toml"
DEFAULT_EXTENSIONS = (".mp4", ".mov", ".m4v")
DEFAULT_DAILY_PROMPT = """你是日课视频复盘教练。仅依据输入的转写文本和元数据分析，不得猜测未提及的事实。
区分计划、行动、结果、阻碍和反思。改进建议最多三条，按影响力排序；每条必须引用转写原话或时间戳，若无法引用则在 evidence 的两个字段填 null。
建议必须是下一次日课中可执行的具体动作。避免人格评判、心理诊断和空泛鼓励。只返回符合给定 JSON Schema 的 JSON。"""
DEFAULT_REVIEW_PROMPT = """你是日课长期复盘教练。仅使用输入的每日分析，不得补充未出现的事实。
指出可观察到的进步和重复模式，并给出最多三项下一周期可执行的重点。避免人格评判、心理诊断和空泛鼓励。只返回符合给定 JSON Schema 的 JSON。"""


@dataclass
class TranscriptionConfig:
    enabled: bool = False
    backend: str = "mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "faster-whisper"
    profile: str = "accurate"
    model: str = "large-v3"
    language: str = "zh"
    device: str = "auto"
    compute_type: str = "auto"


@dataclass
class AnalysisConfig:
    """云端文本分析的配置；密钥始终由环境变量提供。"""

    enabled: bool = False
    api_base: str = ""
    model: str = ""
    api_key_env: str = "JIRI_ANALYSIS_API_KEY"
    timeout_seconds: float = 60.0
    daily_prompt: str | None = None
    review_prompt: str | None = None


@dataclass
class Config:
    inbox: Path
    archive: Path
    mode: str = "move"
    video_extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    duplicate_policy: str = "skip_by_hash"
    missing_capture_time: str = "fallback_then_review"
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """加载 TOML 配置，并把相对路径转换为绝对路径。"""

    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}，请先运行 jiri init")

    with path.open("rb") as file:
        raw: dict[str, Any] = tomllib.load(file)

    paths = raw.get("paths", {})
    import_config = raw.get("import", {})
    transcription = raw.get("transcription", {})
    analysis = raw.get("analysis", {})
    return Config(
        inbox=Path(paths["inbox"]).expanduser(),
        archive=Path(paths["archive"]).expanduser(),
        mode=import_config.get("mode", "move"),
        video_extensions=tuple(import_config.get("video_extensions", DEFAULT_EXTENSIONS)),
        duplicate_policy=import_config.get("duplicate_policy", "skip_by_hash"),
        missing_capture_time=import_config.get("missing_capture_time", "fallback_then_review"),
        transcription=TranscriptionConfig(
            enabled=transcription.get("enabled", False),
            backend=transcription.get("backend", TranscriptionConfig.backend),
            profile=transcription.get("profile", "accurate"),
            model=transcription.get("model", "large-v3"),
            language=transcription.get("language", "zh"),
            device=transcription.get("device", "auto"),
            compute_type=transcription.get("compute_type", "auto"),
        ),
        analysis=AnalysisConfig(
            enabled=analysis.get("enabled", False),
            api_base=analysis.get("api_base", ""),
            model=analysis.get("model", ""),
            api_key_env=analysis.get("api_key_env", "JIRI_ANALYSIS_API_KEY"),
            timeout_seconds=float(analysis.get("timeout_seconds", 60)),
            daily_prompt=_optional_text(analysis.get("daily_prompt")),
            review_prompt=_optional_text(analysis.get("review_prompt")),
        ),
    )


def default_config_text(inbox: Path, archive: Path) -> str:
    """生成适合首次使用的配置示例。"""

    return f'''[paths]
inbox = "{inbox}"
archive = "{archive}"

[import]
mode = "move"
video_extensions = [".mp4", ".mov", ".m4v"]
duplicate_policy = "skip_by_hash"
missing_capture_time = "fallback_then_review"

[transcription]
enabled = false
backend = "mlx"
profile = "accurate"
model = "large-v3"
language = "zh"
device = "auto"
compute_type = "auto"

[analysis]
enabled = false
# api_base = "https://your-openai-compatible-endpoint/v1"
# model = "your-model"
api_key_env = "JIRI_ANALYSIS_API_KEY"
timeout_seconds = 60
daily_prompt = """重点关注目标清晰度、具体产出、阻碍根因和下一步行动。"""
review_prompt = """重点识别持续进步、重复出现的阻碍，以及下一周期最重要的行动。"""
'''


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
