# jiri 技术规划

## 1. 产品边界

### 第一版必须完成

1. 扫描固定待整理目录中的视频。
2. 读取视频拍摄时间，按 `年/月` 归档。
3. 统一生成按时间排序的文件名：`YYYY-MM-DD-NNN.mp4`。
4. 保留原文件名和原始视频内容。
5. 对同内容文件做哈希去重，重复执行时保持幂等。
6. 为每个视频生成一个同名 JSON 文件。
7. JSON 保存技术元数据和完整转写结果。
8. 本地运行高质量中文语音转写，不上传视频。
9. 支持跳过转写、补转写、强制重转写和失败重试。

### 第二版再做

- 通过云端 OpenAI-compatible API 分析转写文本
- 日课总结 / 读书讲述分类
- 摘要、关键词、状态标签、书名和作者提取
- 按日期范围导出适合交给 AI 的合并 JSON
- GUI
- NAS 来源和多根目录配置
- 视频预览、剪辑和压缩

归档不依赖模型。模型只参与可选的文本分析。

## 2. 技术选型

- 语言：Python 3.12+
- CLI：Typer
- 配置：TOML 或 YAML，第一版优先 TOML 以减少依赖
- 视频探测：`ffprobe` / FFmpeg
- 哈希：Python 标准库 `hashlib`，分块读取 SHA-256
- 转写：`faster-whisper`，通过 CTranslate2 在本机运行
- 数据校验：Pydantic
- 测试：pytest
- 包管理和运行：`uv`

选择 Python 是因为 FFmpeg、Whisper 和批处理生态成熟，后续也容易在 Mac 和 NAS 文件路径之间切换。第一版不引入数据库，文件和 JSON 是唯一持久化状态。

## 3. 目录结构

`inbox` 和归档库是两个彼此独立的位置，均由配置决定。`inbox` 只是外部输入来源，不属于归档库，也不要求放在项目目录中。

```text
手机导入目录或 NAS/inbox/       <- 外部输入位置

移动硬盘/VlogArchive/            <- 独立归档库
└── 2026/
    └── 08/
        ├── 2026-08-27-001.mp4
        ├── 2026-08-27-001.json
        └── 2026-08-27-002.mp4
```

视频从外部 `inbox` 移动到独立 `archive`。跨磁盘或 NAS 场景下，移动可能实际执行为“复制、校验、删除源文件”；只有目标文件校验成功后才允许删除源文件。

## 4. 处理流水线

```text
扫描 inbox
  -> 过滤视频格式
  -> 读取媒体元数据
  -> 确定拍摄时间
  -> 计算 SHA-256
  -> 检查重复
  -> 按日期分组并排序
  -> 分配归档文件名
  -> 移动原视频
  -> 写入 JSON
  -> 按配置执行本地转写
  -> 原子更新 JSON
```

拍摄时间的兜底顺序：

1. 媒体元数据中的拍摄时间
2. 文件名中的日期
3. 文件创建时间
4. 放入待确认结果并停止自动归档

JSON 必须记录实际使用的时间来源。

## 5. CLI 设计

```bash
jiri init
jiri setup --transcription --backend mlx
jiri import
jiri transcribe
jiri retry
jiri status
jiri export --from 2026-08-01 --to 2026-08-31
```

### `jiri init`

创建配置文件，并可按需创建归档目录。不会强制在项目目录中创建 `inbox`。

### `jiri setup --transcription`

使用当前 jiri 运行环境安装 `faster-whisper`，并初始化 Whisper 模型。模型首次下载后保存在本机缓存，之后转写不再重复下载。

### `jiri import`

扫描待整理目录，归档新视频，并根据配置决定是否自动转写。

重要选项：

```bash
jiri import --no-transcribe
jiri import --dry-run
jiri import --copy
```

默认移动；`--copy` 用于未来 NAS 或跨磁盘场景。

### `jiri transcribe`

处理已归档但没有完成转写的视频。

```bash
jiri transcribe --profile accurate
jiri transcribe --force
```

### `jiri retry`

只重试失败任务，不重新处理成功结果。

### `jiri status`

显示扫描数量、已归档数量、转写成功数、失败数和待确认数。

## 6. JSON 契约

每个视频对应一个同名 JSON。JSON 使用版本字段，后续增加分析结果时保持向后兼容。

```json
{
  "schema_version": 1,
  "original_filename": "IMG_1234.mp4",
  "archived_filename": "2026-08-27-001.mp4",
  "capture_time": "2026-08-27T20:15:32+08:00",
  "capture_time_source": "media_metadata",
  "duration_seconds": 1210.4,
  "width": 1920,
  "height": 1080,
  "format": "mp4",
  "sha256": "...",
  "transcription": {
    "status": "completed",
    "model": "large-v3",
    "language": "zh",
    "segments": [
      {
        "start_seconds": 0.0,
        "end_seconds": 8.2,
        "text": "今天完成了日课。"
      }
    ]
  },
  "analysis": {}
}
```

`analysis` 第一版默认为空对象，未来保存分类、摘要、关键词和读书信息，不改变视频目录结构。

## 7. 配置设计

推荐配置文件：`~/.config/jiri/config.toml`。

```toml
[paths]
inbox = "/Users/yangshunxiang/Movies/VlogInbox"
archive = "/Volumes/VlogArchive"

[import]
mode = "move"
video_extensions = [".mp4", ".mov", ".m4v"]
duplicate_policy = "skip_by_hash"
missing_capture_time = "fallback_then_review"

[transcription]
enabled = true
profile = "accurate"
model = "large-v3"
language = "zh"
device = "auto"
compute_type = "auto"

[profiles.fast]
model = "small"

[profiles.accurate]
model = "large-v3"

[analysis]
enabled = false
api_base = ""
model = ""
```

API Key 不写入项目文件，使用 macOS Keychain 或环境变量读取。

## 8. 幂等和失败安全

- 扫描前先过滤扩展名和临时文件。
- 使用 SHA-256 判断同内容文件，不依赖文件名。
- 目标文件已存在且哈希一致时跳过移动。
- 视频移动成功后，JSON 写入失败时保留视频，并在下次扫描补写 JSON。
- JSON 使用临时文件写入后原子替换，避免生成半份结果。
- 转写失败只更新 JSON 状态，不影响原视频。
- 不自动删除归档目录中的原视频。
- 所有命令支持结构化日志和非零退出码。

## 9. 实现顺序

1. 建立 Python 包、`uv` 配置和 Typer CLI。
2. 实现配置加载、路径检查和 `init`。
3. 实现视频扫描、FFprobe 元数据读取和拍摄时间兜底。
4. 实现哈希去重、日期分组、命名和移动归档。
5. 实现 JSON 模型和原子写入。
6. 接入 `faster-whisper`，完成转写档位和失败重试。
7. 增加状态命令、dry-run 和测试夹具。
8. 增加文本导出和 OpenAI-compatible 分析接口。

## 10. 主要风险

- M1/16GB 运行 `large-v3` 可能很慢，需要在真实视频上测量并保留 `medium` 或 `small` 档位。
- M1/16GB 建议先用 `balanced`（`medium`）验证完整流程；追求最高准确率时再使用 `accurate`（`large-v3`）。
- 手机视频的拍摄时间元数据可能受时区或导入方式影响，必须在测试样本上验证。
- 跨移动硬盘、NAS 的移动操作可能退化为复制后删除，必须保证复制校验通过后才删除源文件。
- 14 个视频的完整文本可能超过 AI 上下文窗口，导出功能应默认按单视频或单日拆分。
