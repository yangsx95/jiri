"""视频扫描、媒体元数据和文件操作。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import VideoRecord, isoformat

DATE_IN_NAME = re.compile(r"(?<!\d)(20\d{2})[-_年](\d{1,2})[-_月](\d{1,2})")


def scan_videos(inbox: Path, extensions: tuple[str, ...]) -> list[Path]:
    """递归扫描视频，并忽略临时文件和隐藏目录。"""

    allowed = {extension.lower() for extension in extensions}
    return sorted(
        path
        for path in inbox.rglob("*")
        if path.is_file()
        and path.suffix.lower() in allowed
        and not path.name.startswith(".")
        and not any(part.startswith(".") for part in path.relative_to(inbox).parts)
    )


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    """分块计算文件哈希，避免一次性占用大量内存。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def probe_video(path: Path) -> dict[str, Any]:
    """调用 ffprobe 获取视频流和容器元数据。"""

    command = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise RuntimeError("找不到 ffprobe，请先安装 FFmpeg") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"无法读取视频元数据：{path.name}\n{error.stderr.strip()}") from error

    payload = json.loads(result.stdout)
    video_stream = next(
        (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"),
        {},
    )
    tags = payload.get("format", {}).get("tags", {})
    return {
        "duration_seconds": _float_or_none(payload.get("format", {}).get("duration")),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "format": payload.get("format", {}).get("format_name", path.suffix.lstrip(".")),
        "creation_time": tags.get("creation_time") or tags.get("date"),
    }


def capture_time(path: Path, media: dict[str, Any]) -> tuple[datetime, str]:
    """按媒体元数据、文件名、文件创建时间的顺序确定拍摄时间。"""

    if media.get("creation_time"):
        value = str(media["creation_time"]).replace("Z", "+00:00")
        try:
            # 手机视频常用 UTC 的 Z 标记；必须先转成本机时区，再决定归档日期。
            return datetime.fromisoformat(value).astimezone(), "media_metadata"
        except ValueError:
            pass

    match = DATE_IN_NAME.search(path.name)
    if match:
        year, month, day = (int(item) for item in match.groups())
        return datetime(year, month, day).astimezone(), "filename"

    # macOS 的 st_birthtime 是文件创建时间；其他系统回退到修改时间。
    stat = path.stat()
    timestamp = getattr(stat, "st_birthtime", stat.st_mtime)
    return datetime.fromtimestamp(timestamp).astimezone(), "file_creation_time"


def load_existing_hashes(archive: Path) -> dict[str, Path]:
    """从归档库中的 JSON 建立哈希索引，不引入数据库。"""

    result: dict[str, Path] = {}
    for metadata_path in archive.rglob("*.json"):
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            if data.get("sha256"):
                archived_name = data.get("archived_filename")
                result[data["sha256"]] = (
                    metadata_path.parent / archived_name
                    if archived_name
                    else metadata_path.with_suffix(".mp4")
                )
        except (OSError, json.JSONDecodeError):
            continue
    return result


def next_archived_name(
    directory: Path,
    date: datetime,
    extension: str = ".mp4",
    reserved_names: set[str] | None = None,
) -> str:
    """按同一天的时间顺序分配不冲突的文件名。"""

    prefix = date.strftime("%Y-%m-%d")
    extension = extension if extension.startswith(".") else f".{extension}"
    used = {
        int(match.group(1))
        for path in directory.glob(f"{prefix}-*{extension}")
        if (match := re.fullmatch(rf"{re.escape(prefix)}-(\d{{3}}){re.escape(extension)}", path.name))
    }
    for name in reserved_names or set():
        match = re.fullmatch(rf"{re.escape(prefix)}-(\d{{3}}){re.escape(extension)}", name)
        if match:
            used.add(int(match.group(1)))
    number = 1
    while number in used:
        number += 1
    return f"{prefix}-{number:03d}{extension}"


def write_json_atomic(path: Path, record: VideoRecord | dict[str, Any]) -> None:
    """通过临时文件和替换写入 JSON，避免留下半份文件。"""

    payload = record.to_dict() if isinstance(record, VideoRecord) else record
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def transfer_file(source: Path, target: Path, copy: bool = False) -> None:
    """移动或复制文件；跨磁盘移动时先复制并校验，再删除源文件。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        shutil.copy2(source, target)
        return

    try:
        source.replace(target)
        return
    except OSError:
        pass

    temporary = target.with_suffix(target.suffix + ".part")
    shutil.copy2(source, temporary)
    if sha256_file(source) != sha256_file(temporary):
        temporary.unlink(missing_ok=True)
        raise IOError(f"复制校验失败：{source}")
    temporary.replace(target)
    source.unlink()


def build_record(source: Path, archived_name: str, media: dict[str, Any], digest: str, when: datetime, source_name: str) -> VideoRecord:
    """把媒体探测结果组装为 JSON 记录。"""

    return VideoRecord(
        schema_version=1,
        original_filename=source.name,
        archived_filename=archived_name,
        capture_time=isoformat(when),
        capture_time_source=source_name,
        duration_seconds=media.get("duration_seconds"),
        width=media.get("width"),
        height=media.get("height"),
        format=media.get("format", source.suffix.lstrip(".")),
        sha256=digest,
        source_path=str(source),
    )


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
