# CLI 命令参考

## 全局选项

| 选项 | 缩写 | 说明 | 默认值 |
|------|------|------|--------|
| --version | -V | 显示版本并退出 | — |
| --verbose | | 输出详细信息 | 关闭 |
| --debug | | 输出调试日志 | 关闭 |
| --work-dir | -w | 工作目录 | 当前目录 |
| --add-dir | | 添加额外目录到工作区（可多次指定） | 无 |
| --session, --resume | -S, -r | 恢复会话（有 ID 恢复指定，无 ID 交互选择） | 无 |
| --continue | -C | 继续上一个会话 | false |
| --config | | 配置 TOML/JSON 字符串 | 无 |
| --config-file | | 配置文件路径 | ~/.novel/config.toml |
| --model | -m | 指定 LLM 模型 | 配置文件默认 |
| --thinking / --no-thinking | | 启用 / 禁用思考模式 | 配置文件默认 |
| --yolo, --yes | -y | 自动批准所有操作 | false |
| --plan | | 以规划模式启动 | false |
| --prompt | -p, -c | 用户提示词（非交互模式） | 交互式 |
| --print | | 打印模式（非交互，隐含 --yolo） | false |
| --input-format | | 输入格式（需配合 --print） | text |
| --output-format | | 输出格式（需配合 --print） | text |
| --final-message-only | | 仅输出最终助手消息 | false |
| --quiet | | 等同 --print --output-format text --final-message-only | false |
| --book | | 启动时指定书籍 | 无 |
| --agent | | 内置 Agent | default |
| --agent-file | | 外置 Agent 文件路径 | 无 |
| --mcp-config-file | | MCP 配置文件（可多次指定） | 无 |
| --mcp-config | | MCP 配置 JSON（可多次指定） | 无 |
| --skills-dir | | 自定义 skills 目录（可多次指定） | 无 |
| --max-steps-per-turn | | 每轮最大步数（最小 1） | 配置文件默认 |
| --max-retries-per-step | | 每步最大重试次数（最小 1） | 配置文件默认 |

## novel-cli（交互式 Shell）

```bash
# 直接启动
novel

# 指定工作目录和书籍
novel-cli -w /path/to/project --book 西游记

# 使用外置 Agent
novel-cli --agent-file agents/novel-unified-v3/agent.yaml

# 启用思考模式
novel-cli --thinking

# 继续上一个会话
novel-cli -C

# 非交互模式
novel-cli -p "分析人物关系" --print
novel-cli -p "生成代码" --print --output-format stream-json

# 静默模式（仅输出最终回答）
novel-cli -p "解释这个函数" --quiet
```

## novel-cli setup

交互式配置向导，设置 Provider 和 Model。

```bash
novel-cli setup
```

配置流程包含 5 个步骤：
1. 选择 Provider（已有 / 新增）
2. 连接参数（API Key + Base URL）
3. 添加模型（支持循环添加，配置模型能力）
4. 行为偏好（Thinking / YOLO / 主题）
5. 确认保存

## novel-cli info

显示版本和协议信息。

```bash
novel-cli info          # 文本格式
novel-cli info --json   # JSON 格式
```

输出包含：novel-cli 版本、agent spec 版本、wire 协议版本、Python 版本。

## novel-cli export

导出会话数据为 ZIP 文件。

```bash
novel-cli export                    # 导出上一会话
novel-cli export <session_id>       # 导出指定会话
novel-cli export -o backup.zip      # 指定输出文件名
novel-cli export -y                 # 跳过确认
```

| 参数 | 说明 |
|------|------|
| session_id | 会话 ID（可选，默认上一会话） |
| --output, -o | 输出 ZIP 路径（默认 ./session-{id}.zip） |
| --yes, -y | 跳过确认提示 |

## novel-cli import

导入小说数据到知识库。

```bash
novel-cli import /path/to/novel/data          # 导入
novel-cli import /path/to/novel/data --clean  # 清空后导入
```

| 参数 | 说明 |
|------|------|
| path | 小说数据目录路径 |
| --clean | 导入前清空已有数据 |

## novel-cli web

启动 Web 界面。详细用法见 [Web 界面指南](web-guide.md)。

```bash
novel-cli web                       # 本地访问 (http://localhost:5494)
novel-cli web --network             # 局域网访问
novel-cli web --port 8080           # 指定端口
novel-cli web --book 西游记         # 默认书籍
novel-cli web --agent default       # 指定 Agent
novel-cli web --public --auth-token secret  # 公网访问
```

| 参数 | 缩写 | 说明 | 默认值 |
|------|------|------|--------|
| --host | -h | 绑定 IP 地址 | 127.0.0.1 |
| --network | -n | 局域网访问（绑定 0.0.0.0） | false |
| --port | -p | 端口号 | 5494 |
| --reload | | 自动重载 | false |
| --open / --no-open | | 自动打开浏览器 | true |
| --auth-token | | API 认证令牌 | 无 |
| --allowed-origins | | CORS Origin（逗号分隔） | 无 |
| --lan-only / --public | | LAN 限制 / 公网 | lan-only |
| --agent | | 内置 Agent（与 --agent-file 互斥） | default |
| --agent-file | | 外置 Agent 文件 | 无 |
| --book | | 新会话默认书籍 | 无 |
| --work-dir | | 默认工作目录 | 当前目录 |

## novel-cli eval

运行评估任务。详细用法见 [评估框架指南](eval-guide.md)。

```bash
novel-cli eval                      # 运行默认评估
novel-cli eval --task skill_generation  # 指定评估任务
```

## novel-cli debug

启动调试 Web 界面。详细用法见 [调试工具指南](debug-guide.md)。

```bash
uv run python -m novel_debug        # 启动调试 Web 界面（默认 http://localhost:9004）
```

## novel-cli vis

启动 Agent 追踪可视化工具。

```bash
novel-cli vis                       # 默认端口 5495
novel-cli vis --port 3000           # 指定端口
novel-cli vis --network             # 局域网访问
```

| 参数 | 缩写 | 说明 | 默认值 |
|------|------|------|--------|
| --host | -h | 绑定 IP | 127.0.0.1 |
| --network | -n | 局域网访问 | false |
| --port | -p | 端口号 | 5495 |
| --open / --no-open | | 自动打开浏览器 | true |
| --reload | | 自动重载 | false |

## novel-cli acp

启动 ACP（Agent Client Protocol）服务器，用于与 IDE/编辑器集成。

```bash
novel-cli acp
```

## novel-cli term

启动 Toad TUI 终端界面（基于 ACP）。需要 Python 3.14+。

```bash
novel-cli term
novel-cli term -w /path/to/project  # 指定工作目录
```

## novel-cli mcp

管理 MCP（Model Context Protocol）服务器配置。

### novel-cli mcp add

```bash
# 添加 HTTP 类型
novel-cli mcp add --transport http context7 https://mcp.context7.com/mcp \
  --header "CONTEXT7_API_KEY: ctx7sk-your-key"

# 添加 HTTP + OAuth
novel-cli mcp add --transport http --auth oauth linear https://mcp.linear.app/mcp

# 添加 stdio 类型
novel-cli mcp add --transport stdio chrome-devtools -- npx chrome-devtools-mcp@latest
```

| 参数 | 缩写 | 说明 | 默认值 |
|------|------|------|--------|
| name | | MCP 服务器名称 | 必填 |
| TARGET_OR_COMMAND | | HTTP: URL；stdio: 命令（用 -- 分隔） | 必填 |
| --transport | -t | 传输类型：stdio / http | stdio |
| --env | -e | 环境变量 KEY=VALUE（可多次指定） | 无 |
| --header | -H | HTTP 头 KEY:VALUE（可多次指定） | 无 |
| --auth | -a | 认证类型（如 oauth） | 无 |

### novel-cli mcp list

```bash
novel-cli mcp list   # 列出所有 MCP 服务器
```

### novel-cli mcp remove

```bash
novel-cli mcp remove <name>   # 移除 MCP 服务器
```

### novel-cli mcp auth

```bash
novel-cli mcp auth <name>     # OAuth 授权
```

### novel-cli mcp reset-auth

```bash
novel-cli mcp reset-auth <name>  # 重置 OAuth 授权
```

### novel-cli mcp test

```bash
novel-cli mcp test <name>    # 测试连接并列出可用工具
```

## novel-cli plugin

管理插件。

### novel-cli plugin install

```bash
novel-cli plugin install /path/to/plugin    # 本地目录
novel-cli plugin install plugin.zip         # ZIP 文件
novel-cli plugin install https://github.com/user/plugin  # Git URL
```

支持 Git URL（GitHub/GitLab）、.zip 文件、本地目录。安装时自动注入主机配置。

### novel-cli plugin list

```bash
novel-cli plugin list   # 列出已安装插件
```

### novel-cli plugin remove

```bash
novel-cli plugin remove <name>   # 移除插件
```

### novel-cli plugin info

```bash
novel-cli plugin info <name>     # 查看插件详情
```

显示插件名称、版本、描述、配置文件、注入映射和运行时信息。
