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
class Config:
    inbox: Path
    archive: Path
    mode: str = "move"
    video_extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    duplicate_policy: str = "skip_by_hash"
    missing_capture_time: str = "fallback_then_review"
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """加载 TOML 配置，并把相对路径转换为绝对路径。"""

    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}，请先运行 jiri init")

    with path.open("rb") as file:
        raw: dict[str, Any] = tomllib.load(file)

    paths = raw.get("paths", {})
    import_config = raw.get("import", {})
    transcription = raw.get("transcription", {})
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
    )


def default_config_text(inbox: Path, archive: Path) -> str:
    """生成适合首次使用的配置示例。"""

    return f'''[paths]\ninbox = "{inbox}"\narchive = "{archive}"\n\n[import]\nmode = "move"\nvideo_extensions = [".mp4", ".mov", ".m4v"]\nduplicate_policy = "skip_by_hash"\nmissing_capture_time = "fallback_then_review"\n\n[transcription]\nenabled = false\nbackend = "mlx"\nprofile = "accurate"\nmodel = "large-v3"\nlanguage = "zh"\ndevice = "auto"\ncompute_type = "auto"\n'''
