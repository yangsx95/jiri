"""转写运行环境和模型准备。"""

from __future__ import annotations

import subprocess
import sys
from shutil import which


TRANSCRIPTION_PACKAGE = "faster-whisper>=1.1,<2"
MLX_TRANSCRIPTION_PACKAGE = "mlx-whisper>=0.4.3,<0.5"
ANALYSIS_PACKAGES = ("openai>=1,<2", "pydantic>=2,<3", "tenacity>=9,<10")
MODEL_REPOSITORIES = {
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
MLX_MODEL_REPOSITORIES = {
    "small": "mlx-community/whisper-small-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "turbo": "mlx-community/whisper-large-v3-turbo",
}


def install_transcription_backend(backend: str = "faster-whisper") -> None:
    """使用当前 jiri 所在的 Python 环境安装本地转写后端。"""

    uv = which("uv")
    package = MLX_TRANSCRIPTION_PACKAGE if backend == "mlx" else TRANSCRIPTION_PACKAGE
    if uv:
        # uv 创建的环境不一定包含 pip；优先让 uv 安装到当前 Python 环境。
        command = [uv, "pip", "install", "--python", sys.executable, package]
    else:
        command = [sys.executable, "-m", "pip", "install", package]
    subprocess.run(command, check=True)


def install_analysis_dependencies() -> None:
    """把可选云端分析依赖安装到当前 jiri 的 Python 环境。"""

    uv = which("uv")
    if uv:
        command = [uv, "pip", "install", "--python", sys.executable, *ANALYSIS_PACKAGES]
    else:
        command = [sys.executable, "-m", "pip", "install", *ANALYSIS_PACKAGES]
    subprocess.run(command, check=True)


def download_model(
    model_name: str,
    backend: str = "faster-whisper",
    device: str = "auto",
    compute_type: str = "auto",
) -> None:
    """显式下载 Whisper 模型，再从本地缓存加载验证。"""

    try:
        from huggingface_hub import snapshot_download
    except ImportError as error:
        raise RuntimeError("模型下载依赖尚未安装，请先运行 jiri setup --transcription") from error

    # 使用仓库下载接口，让终端显示每个模型文件的下载进度。
    repository_map = MLX_MODEL_REPOSITORIES if backend == "mlx" else MODEL_REPOSITORIES
    repository = repository_map.get(model_name, model_name)
    print(f"正在下载模型文件：{repository}")
    local_path = snapshot_download(repository)
    print("模型文件下载完成，正在检查本地模型...")
    if backend == "mlx":
        from mlx_whisper.load_models import load_model

        load_model(local_path)
    else:
        from faster_whisper import WhisperModel

        WhisperModel(local_path, device=device, compute_type=compute_type)
