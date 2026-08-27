"""MLX 和 faster-whisper 转写适配层。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def transcribe(
    video_path: Path,
    model_name: str,
    language: str,
    device: str,
    compute_type: str,
    profile: str,
    backend: str = "faster-whisper",
    on_segment: Callable[[int, float], None] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """在本地运行 Whisper，并返回 JSON 可序列化的结果。"""

    profile_models = {"fast": "small", "balanced": "medium", "accurate": "large-v3"}
    model_name = profile_models.get(profile, model_name)
    if on_status:
        on_status("model_loading")
    if backend == "mlx":
        output, detected_language = _transcribe_with_mlx(video_path, model_name, language, on_status)
    else:
        output, detected_language = _transcribe_with_faster_whisper(
            video_path, model_name, language, device, compute_type, on_status, on_segment
        )
    if on_segment and backend == "mlx":
        for index, segment in enumerate(output, start=1):
            on_segment(index, segment["end_seconds"])
    return {
        "status": "completed",
        "model": model_name,
        "language": detected_language or language,
        "segments": output,
    }


def _transcribe_with_mlx(video_path: Path, model_name: str, language: str, on_status: Callable[[str], None] | None):
    """使用 MLX 转写；模型可以直接使用 Hugging Face 上的 MLX 检查点。"""

    try:
        import mlx_whisper
    except ImportError as error:
        raise RuntimeError("未安装 MLX 转写后端，请先运行 jiri setup --transcription --backend mlx") from error

    if on_status:
        on_status("transcribing")
    result = mlx_whisper.transcribe(
        str(video_path),
        path_or_hf_repo=f"mlx-community/whisper-{model_name}-mlx",
        language=language,
        # MLX 目前一次性返回结果；打开逐段输出，避免长时间没有任何反馈。
        verbose=True,
    )
    segments = [
        {
            "start_seconds": round(item["start"], 3),
            "end_seconds": round(item["end"], 3),
            "text": item["text"].strip(),
        }
        for item in result.get("segments", [])
    ]
    return segments, result.get("language")


def _transcribe_with_faster_whisper(
    video_path: Path,
    model_name: str,
    language: str,
    device: str,
    compute_type: str,
    on_status: Callable[[str], None] | None,
    on_segment: Callable[[int, float], None] | None,
):
    """使用 CUDA/CPU 版 faster-whisper 转写。"""

    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError("未安装转写后端，请先运行 jiri setup --transcription --backend faster-whisper") from error

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    if on_status:
        on_status("transcribing")
    segments, info = model.transcribe(str(video_path), language=language, vad_filter=True)
    output = []
    for segment in segments:
        output.append({
            "start_seconds": round(segment.start, 3),
            "end_seconds": round(segment.end, 3),
            "text": segment.text.strip(),
        })
        if on_segment:
            on_segment(len(output), segment.end)
    return output, info.language
