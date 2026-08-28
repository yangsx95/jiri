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
区分计划、行动、结果、阻碍和反思。逐一评估输入 rubric 中的每个维度，输出的 dimension_assessments 必须与输入维度一一对应，保留相同的 id 和 label。改进建议数量不得超过输入的 improvement_limit，按影响力排序；每条必须引用转写原话或时间戳，若无法引用则在 evidence 的两个字段填 null。
建议必须是下一次日课中可执行的具体动作。避免人格评判、心理诊断和空泛鼓励。只返回符合给定 JSON Schema 的 JSON。"""
DEFAULT_REVIEW_PROMPT = """你是日课长期复盘教练。仅使用输入的每日分析，不得补充未出现的事实。
指出可观察到的进步和重复模式，并给出最多三项下一周期可执行的重点。避免人格评判、心理诊断和空泛鼓励。只返回符合给定 JSON Schema 的 JSON。"""
DEFAULT_ANALYSIS_DIMENSIONS = (
    ("goals", "目标与计划", "目标是否明确、可执行，是否形成计划—行动—复盘闭环。"),
    ("execution", "执行与产出", "识别具体行动、完成证据与未完成事项，不把意图当作结果。"),
    ("blockers", "阻碍与根因", "区分表面阻碍与可能根因，指出可被下一次行动验证的改进点。"),
    ("reflection", "学习与反思", "评估复盘是否具体，是否提炼了可迁移的方法或洞见。"),
    ("energy", "身体与精力", "仅依据视频提及内容观察睡眠、运动、饮食、精力及其对行动的影响。"),
    ("habits", "环境与习惯", "观察环境、设备、时间安排和重复行为如何支持或阻碍日课。"),
)


@dataclass
class TranscriptionConfig:
    enabled: bool = False
    backend: str = "mlx" if platform.system() == "Darwin" and platform.machine() == "arm64" else "faster-whisper"
    profile: str = "accurate"
    model: str = "large-v3"
    language: str = "zh"
    device: str = "auto"
    compute_type: str = "auto"


@dataclass(frozen=True)
class AnalysisDimension:
    """一个稳定、可纵向比较的日课分析维度。"""

    id: str
    label: str
    guidance: str


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
    dimensions: tuple[AnalysisDimension, ...] = field(
        default_factory=lambda: tuple(AnalysisDimension(*item) for item in DEFAULT_ANALYSIS_DIMENSIONS)
    )
    improvement_limit: int = 3


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
            dimensions=_analysis_dimensions(analysis.get("rubric", {}).get("dimensions")),
            improvement_limit=_improvement_limit(analysis.get("rubric", {}).get("improvement_limit", 3)),
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

[analysis.rubric]
improvement_limit = 3

[[analysis.rubric.dimensions]]
id = "goals"
label = "目标与计划"
guidance = "目标是否明确、可执行，是否形成计划—行动—复盘闭环。"

[[analysis.rubric.dimensions]]
id = "execution"
label = "执行与产出"
guidance = "识别具体行动、完成证据与未完成事项，不把意图当作结果。"

[[analysis.rubric.dimensions]]
id = "blockers"
label = "阻碍与根因"
guidance = "区分表面阻碍与可能根因，指出可被下一次行动验证的改进点。"

[[analysis.rubric.dimensions]]
id = "reflection"
label = "学习与反思"
guidance = "评估复盘是否具体，是否提炼了可迁移的方法或洞见。"

[[analysis.rubric.dimensions]]
id = "energy"
label = "身体与精力"
guidance = "仅依据视频提及内容观察睡眠、运动、饮食、精力及其对行动的影响。"

[[analysis.rubric.dimensions]]
id = "habits"
label = "环境与习惯"
guidance = "观察环境、设备、时间安排和重复行为如何支持或阻碍日课。"
'''


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _analysis_dimensions(value: Any) -> tuple[AnalysisDimension, ...]:
    if value is None:
        return tuple(AnalysisDimension(*item) for item in DEFAULT_ANALYSIS_DIMENSIONS)
    if not isinstance(value, list) or not value:
        raise ValueError("[analysis.rubric].dimensions 必须是至少包含一个维度的列表")
    dimensions: list[AnalysisDimension] = []
    ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("每个分析维度必须包含 id、label 和 guidance")
        dimension = AnalysisDimension(
            id=str(item.get("id", "")).strip(),
            label=str(item.get("label", "")).strip(),
            guidance=str(item.get("guidance", "")).strip(),
        )
        if not all((dimension.id, dimension.label, dimension.guidance)):
            raise ValueError("每个分析维度必须包含非空的 id、label 和 guidance")
        if dimension.id in ids:
            raise ValueError(f"分析维度 id 重复：{dimension.id}")
        ids.add(dimension.id)
        dimensions.append(dimension)
    return tuple(dimensions)


def _improvement_limit(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("[analysis.rubric].improvement_limit 必须是整数") from error
    if not 1 <= result <= 5:
        raise ValueError("[analysis.rubric].improvement_limit 必须在 1 到 5 之间")
    return result
