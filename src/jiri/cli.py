"""jiri 命令行入口。"""

from __future__ import annotations

import multiprocessing
from datetime import date, datetime
from queue import Empty
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeRemainingColumn

from .config import DEFAULT_CONFIG_PATH, default_config_text, load_config
from .setup import download_model, install_analysis_dependencies, install_transcription_backend
from .service import analyze_all, import_videos, review_period, show_daily_analyses, status, transcribe_all

app = typer.Typer(help="积日：本地优先的日课视频归档与转写工具。", no_args_is_help=True)
DEFAULT_INBOX = Path.home() / "Movies" / "VlogInbox"
DEFAULT_ARCHIVE = Path.home() / "Movies" / "VlogArchive"


@app.command()
def init(
    inbox: Path = typer.Option(DEFAULT_INBOX, help="外部待整理目录。"),
    archive: Path = typer.Option(DEFAULT_ARCHIVE, help="独立归档目录。"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, help="配置文件路径。"),
) -> None:
    """创建配置文件和归档目录，不在项目目录创建 inbox。"""

    if config.exists():
        raise typer.BadParameter(f"配置文件已存在：{config}")
    config.parent.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    config.write_text(default_config_text(inbox.expanduser(), archive.expanduser()), encoding="utf-8")
    typer.echo(f"已创建配置：{config}")
    typer.echo(f"待整理目录：{inbox}")
    typer.echo(f"归档目录：{archive}")


@app.command()
def setup(
    transcription: bool = typer.Option(False, "--transcription", help="安装本地转写后端并下载模型。"),
    analysis: bool = typer.Option(False, "--analysis", help="安装云端文本分析依赖。"),
    model: str = typer.Option("large-v3", help="要下载的 Whisper 模型。"),
    backend: str = typer.Option("mlx", help="转写后端：mlx 或 faster-whisper。"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, help="配置文件路径。"),
) -> None:
    """准备 jiri 的本地转写环境。"""

    if not transcription and not analysis:
        raise typer.BadParameter("请指定 --transcription 或 --analysis")

    _create_sample_config_if_missing(config)

    if analysis:
        typer.echo("正在安装 AI 分析依赖...")
        try:
            install_analysis_dependencies()
        except Exception as error:
            typer.echo(f"分析环境准备失败：{error}", err=True)
            raise typer.Exit(code=1) from error
        typer.echo("AI 分析依赖安装完成")

    if not transcription:
        return

    typer.echo("正在安装本地转写后端...")
    try:
        install_transcription_backend(backend)
        settings = load_config(config) if config.exists() else None
        device = settings.transcription.device if settings else "auto"
        compute_type = settings.transcription.compute_type if settings else "auto"
        typer.echo(f"正在准备 Whisper 模型：{model}")
        download_model(model, backend=backend, device=device, compute_type=compute_type)
    except Exception as error:
        typer.echo(f"转写环境准备失败：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"转写环境准备完成，模型已缓存：{model}")


def _create_sample_config_if_missing(config: Path) -> None:
    """让首次 setup 也能获得可直接编辑的样例配置。"""

    if config.exists():
        return
    config.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_ARCHIVE.mkdir(parents=True, exist_ok=True)
    config.write_text(default_config_text(DEFAULT_INBOX, DEFAULT_ARCHIVE), encoding="utf-8")
    typer.echo(f"未找到配置，已创建样例配置：{config}")


@app.command(name="import")
def import_command(
    dry_run: bool = typer.Option(False, "--dry-run", help="只预览，不移动或复制文件。"),
    copy: bool = typer.Option(False, "--copy", help="复制视频，不删除 inbox 中的源文件。"),
    no_transcribe: bool = typer.Option(False, "--no-transcribe", help="本次跳过转写。"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, help="配置文件路径。"),
) -> None:
    """扫描外部 inbox 并归档新视频。"""

    settings = load_config(config)
    counts = import_videos(settings, dry_run=dry_run, copy=copy, transcribe=False if no_transcribe else None)
    typer.echo(f"扫描 {counts['scanned']} 个，归档 {counts['archived']} 个，重复 {counts['duplicates']} 个，失败 {counts['failed']} 个")


@app.command()
def transcribe(
    force: bool = typer.Option(False, "--force", help="重新转写已完成的视频。"),
    backend: str | None = typer.Option(None, "--backend", help="转写后端：mlx 或 faster-whisper。"),
    profile: str | None = typer.Option(None, help="转写档位：accurate、balanced 或 fast。"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, help="配置文件路径。"),
) -> None:
    """转写归档库中的视频。"""

    counts = _run_transcription(load_config(config), force=force, profile=profile, backend=backend)
    typer.echo(f"发现 {counts['found']} 个，完成 {counts['completed']} 个，跳过 {counts['skipped']} 个，失败 {counts['failed']} 个")


@app.command()
def retry(config: Path = typer.Option(DEFAULT_CONFIG_PATH, help="配置文件路径。")) -> None:
    """重试失败或尚未完成的转写任务。"""

    counts = _run_transcription(load_config(config), force=False, profile=None, backend=None)
    typer.echo(f"发现 {counts['found']} 个，完成 {counts['completed']} 个，跳过 {counts['skipped']} 个，失败 {counts['failed']} 个")


@app.command()
def analyze(
    force: bool = typer.Option(False, "--force", help="重新分析已有完成结果的视频。"),
    date_from: str | None = typer.Option(None, "--from", help="仅分析此日期及之后的视频（YYYY-MM-DD）。"),
    date_to: str | None = typer.Option(None, "--to", help="仅分析此日期及之前的视频（YYYY-MM-DD）。"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, help="配置文件路径。"),
) -> None:
    """对已转写视频生成带证据的日课复盘。"""

    try:
        counts = analyze_all(load_config(config), force=force, date_from=_parse_date(date_from), date_to=_parse_date(date_to))
    except RuntimeError as error:
        typer.echo(f"分析无法启动：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"发现 {counts['found']} 个，完成 {counts['completed']} 个，跳过 {counts['skipped']} 个，失败 {counts['failed']} 个")


@app.command()
def review(
    date_from: str | None = typer.Option(None, "--from", help="回顾起始日期，默认最近 7 天。"),
    date_to: str | None = typer.Option(None, "--to", help="回顾结束日期，默认今天。"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, help="配置文件路径。"),
) -> None:
    """从已有日分析生成周/月趋势回顾。"""

    try:
        output = review_period(load_config(config), date_from=_parse_date(date_from), date_to=_parse_date(date_to))
    except RuntimeError as error:
        typer.echo(f"回顾无法启动：{error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"已生成回顾：{output}")


@app.command()
def show(
    target_date: str = typer.Option(..., "--date", help="要查看的日期（YYYY-MM-DD）。"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, help="配置文件路径。"),
) -> None:
    """只读查看某一天已保存的 AI 分析，不重新请求 AI。"""

    try:
        show_daily_analyses(load_config(config), _parse_date(target_date))
    except RuntimeError as error:
        typer.echo(f"无法查看分析：{error}", err=True)
        raise typer.Exit(code=1) from error


def _transcription_worker(settings, force: bool, profile: str | None, backend: str | None, events) -> None:
    """在独立进程中运行转写，使主进程可以可靠响应 Ctrl-C。"""

    def on_progress(event: dict) -> None:
        events.put({"type": "progress", "value": event})

    try:
        result = transcribe_all(settings, force=force, profile=profile, backend=backend, progress_callback=on_progress)
        events.put({"type": "result", "value": result})
    except Exception as error:
        events.put({"type": "error", "value": str(error)})


def _run_transcription(settings, force: bool, profile: str | None, backend: str | None) -> dict[str, int]:
    """用 Rich 展示总体文件进度和当前文件的转写进度。"""

    console = Console()
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
        auto_refresh=False,
    ) as progress:
        overall_task = progress.add_task("总体进度", total=1, visible=False)
        video_tasks: dict[str, int] = {}

        def on_progress(event: dict) -> None:
            state = event["event"]
            filename = event["filename"]
            segments = event["segments"]
            duration = event.get("duration_seconds") or 1
            video_task = video_tasks.get(filename)
            if video_task is None:
                video_task = progress.add_task(filename, total=duration)
                video_tasks[filename] = video_task

            if state == "start":
                description = f"{filename} | 准备中"
            elif state == "model_loading":
                description = f"{filename} | 正在加载模型"
            elif state == "transcribing":
                description = f"{filename} | 已开始识别"
            elif state == "segment":
                processed = event.get("processed_seconds", 0)
                description = (
                    f"{filename} | {format_seconds(processed)} / "
                    f"{format_seconds(duration)} | {segments} 段"
                )
            elif state == "completed":
                description = f"完成 {filename} | {segments} 段"
            elif state == "skipped":
                description = f"跳过 {filename} | 已完成"
            else:
                description = f"失败 {filename}"
            completed = duration if state in {"completed", "skipped"} else event.get("processed_seconds", 0)
            progress.update(video_task, description=description, completed=min(completed, duration))
            progress.update(overall_task, total=event["total"] or 1, completed=event["current"])
            # 只在收到真实事件时刷新，避免长时间推理时终端重复刷屏。
            progress.refresh()

        events = multiprocessing.Queue()
        worker = multiprocessing.Process(
            target=_transcription_worker,
            args=(settings, force, profile, backend, events),
            daemon=True,
        )
        worker.start()
        result = None
        try:
            while result is None and (worker.is_alive() or not events.empty()):
                try:
                    event = events.get(timeout=0.2)
                except Empty:
                    continue
                if event["type"] == "progress":
                    on_progress(event["value"])
                elif event["type"] == "error":
                    console.print(f"转写进程失败：{event['value']}", style="red")
                elif event["type"] == "result":
                    result = event["value"]
            worker.join()
        except KeyboardInterrupt:
            console.print("\n正在停止转写进程...", style="yellow")
            worker.terminate()
            worker.join(timeout=3)
            if worker.is_alive():
                worker.kill()
                worker.join()
            raise typer.Exit(code=130)

        return result or {"found": 0, "completed": 0, "skipped": 0, "failed": 1}


def format_seconds(value: float) -> str:
    """将秒数显示为适合终端阅读的时分秒。"""

    total = max(0, int(value))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _parse_date(value: str | None) -> date | None:
    """把 CLI 日期参数限定为稳定的 YYYY-MM-DD 格式。"""

    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as error:
        raise typer.BadParameter("日期必须是 YYYY-MM-DD，例如 2026-08-27") from error


@app.command(name="status")
def status_command(config: Path = typer.Option(DEFAULT_CONFIG_PATH, help="配置文件路径。")) -> None:
    """显示归档库处理状态。"""

    values = status(load_config(config))
    typer.echo(
        f"视频 {values['videos']} 个，转写完成 {values['completed']} 个，待处理 {values['pending']} 个，失败 {values['failed']} 个；"
        f"分析完成 {values['analyzed']} 个，待分析 {values['analysis_pending']} 个，失败 {values['analysis_failed']} 个"
    )


if __name__ == "__main__":
    app()
