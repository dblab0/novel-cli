# 配置文件详解

## 配置文件位置

```
~/.novel/config.toml
```

可通过 `novel-cli setup` 交互式配置，或手动编辑此文件。

## 完整配置格式

```toml
# ===== 全局设置 =====
default_model = "zhipu/glm-5.1"      # 默认模型（格式: provider/model）
default_thinking = false               # 默认不启用思考模式
default_yolo = false                   # 默认不自动批准
theme = "dark"                         # 终端主题: dark / light

# ===== Provider 定义 =====

[providers.zhipu]
type = "zhipu"
base_url = "https://open.bigmodel.cn/api/paas/v4/"
api_key = "your-api-key-here"

[providers.openai]
type = "openai_legacy"
base_url = "https://api.openai.com/v1"
api_key = "sk-..."

[providers.anthropic]
type = "anthropic"
base_url = "https://api.anthropic.com"
api_key = "sk-ant-..."

[providers.google]
type = "google_genai"
base_url = ""                         # Google GenAI 不需要 base_url
api_key = "AIza..."

[providers.kimi]
type = "kimi"
base_url = "https://api.moonshot.cn/v1"
api_key = "sk-..."

[providers.openrouter]
type = "openrouter"
base_url = "https://openrouter.ai/api/v1"
api_key = "sk-or-..."

# ===== Model 定义 =====

[models."zhipu/glm-5.1"]
provider = "zhipu"
model = "glm-5.1"
max_context_size = 128000
capabilities = ["thinking"]

[models."openai/gpt-4o"]
provider = "openai"
model = "gpt-4o"
max_context_size = 128000
capabilities = ["thinking", "image_in"]

[models."anthropic/claude-sonnet-4-20250514"]
provider = "anthropic"
model = "claude-sonnet-4-20250514"
max_context_size = 200000
capabilities = ["thinking", "image_in"]
```

## 全局设置

| 字段 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| default_model | string | 默认模型（格式: provider/model） | 无（setup 时配置） |
| default_thinking | bool | 默认启用思考模式 | false |
| default_yolo | bool | 默认自动批准工具调用 | false |
| default_plan_mode | bool | 默认启用计划模式 | false |
| default_editor | string | 默认外部编辑器命令（如 `vim`、`code --wait`） | 空 |
| merge_all_available_skills | bool | 合并所有品牌目录的 skills | false |
| theme | string | 终端主题 | dark |

## Provider 定义

每个 Provider 独立一个 `[providers.<name>]` 段：

| 字段 | 类型 | 说明 |
|------|------|------|
| type | string | Provider 类型（见下表） |
| base_url | string | API 地址（内置 Provider 自动预填） |
| api_key | string | API 密钥 |
| env | map | 环境变量设置（可选） |
| custom_headers | map | 自定义请求头（可选） |

### Provider 类型对照表

| Provider | type 值 | 说明 |
|----------|---------|------|
| 智谱 AI | `zhipu` | GLM 系列 |
| Kimi / Moonshot | `kimi` | Moonshot 系列 |
| Anthropic | `anthropic` | Claude 系列 |
| OpenAI | `openai_legacy` | GPT 系列（Chat Completions API） |
| OpenAI Responses | `openai_responses` | GPT 系列（Responses API） |
| Google GenAI | `google_genai` | Gemini 系列 |
| OpenRouter | `openrouter` | 多模型聚合平台 |
| Gemini | `gemini` | Gemini 系列（google_genai 别名） |
| Vertex AI | `vertexai` | Google Vertex AI |

## Model 定义

每个模型独立一个 `[models."provider/model"]` 段：

| 字段 | 类型 | 说明 |
|------|------|------|
| provider | string | 对应 Provider 名称 |
| model | string | 模型标识符 |
| max_context_size | int | 最大上下文长度 |
| capabilities | list | 模型能力列表（可选，默认为空） |

### 模型能力说明

| 能力 | 说明 |
|------|------|
| `thinking` | 支持思考模式（可通过 `--thinking` 开关） |
| `always_thinking` | 强制思考模式（无法关闭） |
| `image_in` | 支持图像输入（多模态） |
| `video_in` | 支持视频输入 |

> **注意**：`thinking` 和 `always_thinking` 不能同时配置。

## 自定义 Provider

配置 DeepSeek、Qwen、Groq 等兼容 OpenAI API 的服务商：

```toml
[providers.deepseek]
type = "openai_legacy"
base_url = "https://api.deepseek.com/v1"
api_key = "sk-..."

[models."deepseek/deepseek-chat"]
provider = "deepseek"
model = "deepseek-chat"
max_context_size = 64000
capabilities = ["thinking"]
```

## 命令行覆盖

配置文件中的设置可通过命令行参数临时覆盖：

```bash
# 覆盖模型
novel-cli -m openai/gpt-4o

# 覆盖思考模式
novel-cli --thinking
novel-cli --no-thinking

# 覆盖配置文件
novel-cli --config-file /path/to/other/config.toml

# 内联配置
novel-cli --config 'default_model = "openai/gpt-4o"'
```

## 环境变量覆盖

配置优先级：**命令行参数 > 环境变量 > 配置文件 > 默认值**

| 环境变量 | 适用 Provider | 说明 |
|----------|---------------|------|
| `NOVEL_BASE_URL` | kimi | 覆盖 base_url |
| `NOVEL_API_KEY` | kimi | 覆盖 api_key |
| `NOVEL_MODEL_NAME` | kimi | 覆盖模型名称 |
| `NOVEL_MODEL_MAX_CONTEXT_SIZE` | kimi | 覆盖最大上下文长度 |
| `NOVEL_MODEL_CAPABILITIES` | kimi | 覆盖模型能力 |
| `OPENAI_BASE_URL` | openai_legacy / openai_responses | 覆盖 base_url |
| `OPENAI_API_KEY` | openai_legacy / openai_responses | 覆盖 api_key |
| `NOVEL_SHARE_DIR` | 全局 | 覆盖配置文件目录路径（默认 `~/.novel`） |

## 高级配置

### 循环控制

```toml
[loop_control]
max_steps_per_turn = 100       # 每轮最大步数
max_retries_per_step = 3       # 每步最大重试次数
max_ralph_iterations = 0       # 最大 Ralph 迭代次数
reserved_context_size = 50000  # 预留上下文大小
compaction_trigger_ratio = 0.85  # 上下文压缩触发比例
```

### 后台任务

```toml
[background]
max_running_tasks = 3            # 最大并行任务数
read_max_bytes = 1048576         # 读取最大字节数
notification_tail_lines = 5      # 通知尾部行数
notification_tail_chars = 1000   # 通知尾部字符数
```

### MCP 配置

```toml
[mcp]
# MCP 客户端工具调用超时（毫秒）
[mcp.client]
tool_call_timeout_ms = 60000
```

### Hooks

```toml
# 生命周期钩子配置（列表形式）
[[hooks]]
event = "pre_tool_call"
command = "echo 'about to call tool'"
```
