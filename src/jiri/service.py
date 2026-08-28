"""归档和转写业务流程。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
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
    metadata_paths = [path for path in sorted(config.archive.rglob("*.json")) if _is_video_metadata(path)]
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

    result = {"videos": 0, "completed": 0, "pending": 0, "failed": 0, "analyzed": 0, "analysis_pending": 0, "analysis_failed": 0}
    for metadata_path in config.archive.rglob("*.json"):
        try:
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not value.get("archived_filename"):
                continue
            result["videos"] += 1
            state = value.get("transcription", {}).get("status", "pending")
        except (OSError, json.JSONDecodeError):
            result["videos"] += 1
            result["failed"] += 1
            continue
        if state == "completed":
            result["completed"] += 1
        elif state == "failed":
            result["failed"] += 1
        else:
            result["pending"] += 1
        analysis_state = value.get("analysis", {}).get("status", "pending")
        if analysis_state == "completed":
            result["analyzed"] += 1
        elif analysis_state == "failed":
            result["analysis_failed"] += 1
        else:
            result["analysis_pending"] += 1
    return result


def analyze_all(config: Config, force: bool = False, date_from: date | None = None, date_to: date | None = None) -> dict[str, int]:
    """为时间范围内已转写的视频补齐单日 AI 复盘。"""

    from .analyzer import analyze_daily_record, validate_analysis_config

    validate_analysis_config(config.analysis)
    counts = {"found": 0, "completed": 0, "failed": 0, "skipped": 0}
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for metadata_path in sorted(config.archive.rglob("*.json")):
        if not _is_video_metadata(metadata_path):
            continue
        record = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not _in_date_range(record, date_from, date_to):
            continue
        if record.get("transcription", {}).get("status") != "completed":
            counts["skipped"] += 1
            continue
        candidates.append((metadata_path, record))

    counts["found"] = len(candidates)
    for current, (metadata_path, record) in enumerate(candidates, start=1):
        if not force and record.get("analysis", {}).get("status") == "completed":
            counts["skipped"] += 1
            print(f"[{current}/{counts['found']}] 跳过：{metadata_path.name}（已有分析结果）")
            continue
        print(f"\n[{current}/{counts['found']}] 正在分析：{metadata_path.name}")
        print("  正在将转写文本发送给 AI，请稍候...")
        try:
            record["analysis"] = analyze_daily_record(record, config.analysis)
            write_json_atomic(metadata_path, record)
            counts["completed"] += 1
            _print_daily_analysis(record["analysis"])
        except Exception as error:
            record["analysis"] = {"status": "failed", "error": str(error)}
            write_json_atomic(metadata_path, record)
            counts["failed"] += 1
            print(f"  分析失败：{error}")
    return counts


def review_period(config: Config, date_from: date | None = None, date_to: date | None = None) -> Path:
    """汇总已有日分析并写入归档库的 .jiri/reviews 中。"""

    from .analyzer import analyze_period, validate_analysis_config

    validate_analysis_config(config.analysis)
    end = date_to or date.today()
    start = date_from or end - timedelta(days=6)
    records: list[dict[str, Any]] = []
    for metadata_path in sorted(config.archive.rglob("*.json")):
        if not _is_video_metadata(metadata_path):
            continue
        record = json.loads(metadata_path.read_text(encoding="utf-8"))
        if _in_date_range(record, start, end) and record.get("analysis", {}).get("status") == "completed":
            records.append(record)
    if not records:
        raise RuntimeError("该时间范围内没有已完成的日分析，请先运行 jiri analyze")
    print(f"正在汇总 {len(records)} 条日分析（{start.isoformat()} 至 {end.isoformat()}）...")
    review = analyze_period(records, config.analysis, start.isoformat(), end.isoformat())
    output_path = config.archive / ".jiri" / "reviews" / f"{start.isoformat()}_to_{end.isoformat()}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_path, review)
    print("\n趋势回顾")
    print(f"  {review.get('overview', '无摘要')}")
    _print_list("可见进步", review.get("progress", []))
    _print_list("重复模式", review.get("recurring_patterns", []))
    _print_list("下周期重点", review.get("focus_next_period", []))
    return output_path


def _is_video_metadata(path: Path) -> bool:
    """排除周报、导出等非视频旁车 JSON。"""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(value.get("archived_filename"))


def _in_date_range(record: dict[str, Any], date_from: date | None, date_to: date | None) -> bool:
    try:
        capture_date = datetime.fromisoformat(record["capture_time"]).date()
    except (KeyError, TypeError, ValueError):
        return False
    return (date_from is None or capture_date >= date_from) and (date_to is None or capture_date <= date_to)


def _print_daily_analysis(analysis: dict[str, Any]) -> None:
    """在 CLI 中展示足够行动导向的摘要，完整结果仍写入 JSON。"""

    print(f"  完成：{analysis.get('summary', '无摘要')}")
    _print_list("亮点", analysis.get("highlights", []))
    improvements = analysis.get("improvements", [])
    if improvements:
        print("  可改进之处：")
        for item in improvements:
            print(f"    {item.get('priority', '-')}. {item.get('issue', '未说明问题')}")
            evidence = item.get("evidence", {})
            timestamp = evidence.get("timestamp_seconds")
            quote = evidence.get("quote")
            if timestamp is not None or quote:
                parts = []
                if timestamp is not None:
                    parts.append(_format_timestamp(float(timestamp)))
                if quote:
                    parts.append(f"“{quote}”")
                print(f"       依据：{' — '.join(parts)}")
            print(f"       下一步：{item.get('action', '未提供')}")
    _print_list("明日重点", analysis.get("tomorrow_focus", []))


def _print_list(label: str, items: list[Any]) -> None:
    if items:
        print(f"  {label}：{'；'.join(str(item) for item in items)}")


def _format_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
