"""归档和转写业务流程。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .media import (
    build_record,
    capture_time,
    load_existing_hashes,
    next_archived_name,
    probe_video,
    scan_videos,
    sha256_file,
    transfer_file,
    write_json_atomic,
)


def import_videos(config: Config, dry_run: bool = False, copy: bool = False, transcribe: bool | None = None) -> dict[str, int]:
    """归档 inbox 中的新视频，并按配置决定是否转写。"""

    config.archive.mkdir(parents=True, exist_ok=True)
    existing_hashes = load_existing_hashes(config.archive)
    reserved_names: set[str] = set()
    counts = {"scanned": 0, "archived": 0, "duplicates": 0, "failed": 0}
    should_transcribe = config.transcription.enabled if transcribe is None else transcribe

    for source in scan_videos(config.inbox, config.video_extensions):
        counts["scanned"] += 1
        try:
            media = probe_video(source)
            when, time_source = capture_time(source, media)
            digest = sha256_file(source)
            duplicate = existing_hashes.get(digest)
            if duplicate:
                counts["duplicates"] += 1
                print(f"跳过重复视频：{source.name} -> {duplicate}")
                continue

            target_dir = config.archive / when.strftime("%Y") / when.strftime("%m")
            archived_name = next_archived_name(
                target_dir,
                when,
                source.suffix.lower(),
                reserved_names,
            )
            target = target_dir / archived_name
            reserved_names.add(archived_name)
            metadata_path = target.with_suffix(".json")
            record = build_record(source, archived_name, media, digest, when, time_source)

            if dry_run:
                print(f"[预览] {source} -> {target}")
                continue

            transfer_file(source, target, copy=copy or config.mode == "copy")
            write_json_atomic(metadata_path, record)
            existing_hashes[digest] = target
            counts["archived"] += 1
            print(f"已归档：{target}")

            if should_transcribe:
                transcribe_record(metadata_path, target, config)
        except Exception as error:  # 单个文件失败不应阻塞同批其他视频。
            counts["failed"] += 1
            print(f"处理失败：{source.name}：{error}")

    return counts


def transcribe_all(
    config: Config,
    force: bool = False,
    profile: str | None = None,
    backend: str | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, int]:
    """扫描归档 JSON，补齐待转写或失败的视频。"""

    counts = {"found": 0, "completed": 0, "failed": 0, "skipped": 0}
    metadata_paths = sorted(config.archive.rglob("*.json"))
    total = len(metadata_paths)
    for index, metadata_path in enumerate(metadata_paths, start=1):
        try:
            record = json.loads(metadata_path.read_text(encoding="utf-8"))
            status = record.get("transcription", {}).get("status", "pending")
            if not force and status == "completed":
                counts["skipped"] += 1
                _notify(
                    progress_callback,
                    "skipped",
                    index,
                    total,
                    metadata_path.name,
                    0,
                    record.get("duration_seconds"),
                    record.get("duration_seconds"),
                )
                continue
            archived_name = record.get("archived_filename")
            video_path = metadata_path.parent / archived_name if archived_name else metadata_path.with_suffix(".mp4")
            if not video_path.exists():
                raise FileNotFoundError(f"视频不存在：{video_path}")
            counts["found"] += 1
            segment_count = 0

            def on_segment(count: int, processed_seconds: float) -> None:
                nonlocal segment_count
                segment_count = count
                _notify(
                    progress_callback,
                    "segment",
                    index,
                    total,
                    metadata_path.name,
                    count,
                    processed_seconds,
                    record.get("duration_seconds"),
                )

            def on_status(status: str) -> None:
                _notify(
                    progress_callback,
                    status,
                    index,
                    total,
                    metadata_path.name,
                    segment_count,
                    0,
                    record.get("duration_seconds"),
                )

            _notify(
                progress_callback,
                "start",
                index,
                total,
                metadata_path.name,
                0,
                0,
                record.get("duration_seconds"),
            )
            transcribe_record(
                metadata_path,
                video_path,
                config,
                profile=profile,
                on_segment=on_segment,
                on_status=on_status,
                backend=backend or config.transcription.backend,
            )
            counts["completed"] += 1
            _notify(
                progress_callback,
                "completed",
                index,
                total,
                metadata_path.name,
                segment_count,
                record.get("duration_seconds") or 0,
                record.get("duration_seconds"),
            )
        except Exception as error:
            counts["failed"] += 1
            _notify(progress_callback, "failed", index, total, metadata_path.name, 0, 0, None, str(error))
            print(f"转写失败：{metadata_path.name}：{error}")
    return counts


def transcribe_record(
    metadata_path: Path,
    video_path: Path,
    config: Config,
    profile: str | None = None,
    on_segment: Callable[[int, float], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    backend: str | None = None,
) -> None:
    """延迟加载 Whisper，确保未安装转写依赖时归档仍可用。"""

    try:
        from .transcriber import transcribe
    except ImportError as error:
        raise RuntimeError("未安装转写后端，请先运行 jiri setup --transcription --backend mlx") from error

    record = json.loads(metadata_path.read_text(encoding="utf-8"))
    selected_profile = profile or config.transcription.profile
    try:
        result = transcribe(
            video_path,
            model_name=config.transcription.model,
            language=config.transcription.language,
            device=config.transcription.device,
            compute_type=config.transcription.compute_type,
            profile=selected_profile,
            backend=backend or config.transcription.backend,
            on_segment=on_segment,
            on_status=on_status,
        )
    except Exception as error:
        # 错误也写入 JSON，下一次 retry 可以只处理失败任务。
        record["transcription"] = {
            "status": "failed",
            "model": config.transcription.model,
            "language": config.transcription.language,
            "error": str(error),
            "segments": [],
        }
        write_json_atomic(metadata_path, record)
        raise

    record["transcription"] = result
    write_json_atomic(metadata_path, record)


def _notify(
    callback: Callable[[dict[str, Any]], None] | None,
    event: str,
    current: int,
    total: int,
    filename: str,
    segments: int,
    processed_seconds: float,
    duration_seconds: float | None,
    error: str | None = None,
) -> None:
    """向 CLI 或未来界面发送转写进度事件。"""

    if callback:
        callback({
            "event": event,
            "current": current,
            "total": total,
            "filename": filename,
            "segments": segments,
            "processed_seconds": processed_seconds,
            "duration_seconds": duration_seconds,
            "error": error,
        })


def status(config: Config) -> dict[str, int]:
    """统计归档库中 JSON 的处理状态。"""

    result = {"videos": 0, "completed": 0, "pending": 0, "failed": 0}
    for metadata_path in config.archive.rglob("*.json"):
        result["videos"] += 1
        try:
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
            state = value.get("transcription", {}).get("status", "pending")
        except (OSError, json.JSONDecodeError):
            state = "failed"
        if state == "completed":
            result["completed"] += 1
        elif state == "failed":
            result["failed"] += 1
        else:
            result["pending"] += 1
    return result
