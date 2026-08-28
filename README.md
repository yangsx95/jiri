# jiri

积日：你的日课视频档案库。

`jiri` 是一个本地优先的命令行工具，用于将每周导入的视频按拍摄时间归档，并在 Mac 上生成可供 AI 使用的结构化 JSON 和本地转写结果。

## 当前目标

- 从固定 `inbox` 扫描原始视频
- 按拍摄时间归档到 `YYYY/MM`
- 保留原始视频内容，只移动和统一命名
- 用 SHA-256 识别重复文件
- 生成同名 JSON 旁车文件
- 使用本地 Whisper 转写普通话
- 支持断点、失败重试和可配置转写档位
- 为未来的文本分类、摘要和 NAS 文件来源预留接口

首次使用转写时执行：

```bash
jiri setup --transcription --backend mlx
```

该命令会安装本地转写后端并下载 Whisper 模型。之后直接运行 `jiri transcribe` 即可。

## AI 日课分析

分析只会发送已生成的转写文本和视频元数据，不会上传原始视频。分析依赖会随 `jiri` 一同安装；已安装旧版本时，可执行下列命令补齐依赖：

```bash
jiri setup --analysis
```

在 `~/.config/jiri/config.toml` 配置 OpenAI-compatible API，并将密钥放入环境变量：

```toml
[analysis]
enabled = true
api_base = "https://your-openai-compatible-endpoint/v1" # 使用 OpenAI 默认地址时可留空
model = "your-model"
api_key_env = "JIRI_ANALYSIS_API_KEY"
```

```bash
export JIRI_ANALYSIS_API_KEY="..."
jiri analyze
jiri show --date 2026-08-27        # 只读查看当天已保存的分析，不请求 AI
jiri analyze --from 2026-08-01 --to 2026-08-31
jiri review
jiri review --from 2026-08-01 --to 2026-08-31
```

单日分析会写回视频的同名 JSON；周/月回顾保存在归档目录的 `.jiri/reviews/` 中。每条改进建议都包含转写证据，或明确标记为信息不足。

如需自定义提示词，推荐直接在 `[analysis]` 中使用 TOML 多行字符串。日分析和周期回顾可独立替换；自定义提示词仍会自动附加 JSON Schema，以保证命令能够稳定读取结果。

```toml
[analysis]
daily_prompt = """
你是我的日课复盘教练。重点检查目标、产出、阻碍和明日行动。
直率指出问题，避免空泛鼓励；每条建议必须基于转写证据。
"""

review_prompt = """
你是我的长期复盘教练。归纳可见进步、重复模式和下周期重点。
仅依据输入的每日分析，不得猜测。
"""
```

也可改为指定外部文本文件（当两者同时存在时，TOML 内容优先）：

```toml
[analysis]
daily_prompt_file = "prompts/daily-review.txt"
review_prompt_file = "prompts/period-review.txt"
```

## 规划

详细技术方案见 [docs/technical-plan.md](docs/technical-plan.md)。
