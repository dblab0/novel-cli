# Novel Debug

Novel Debug 是一个 Web 调试工具，用于手动测试工具调用和构建 agent 轨迹（wire.jsonl）。

## 功能概览

### 1. 工具测试场

直接调用内置工具（SearchEntity / SearchGraph / SearchCorpus），查看实时返回结果，无需启动完整 agent。

### 2. 轨迹构建器

手动模拟 agent 的工具调用流程，逐步构建 `wire.jsonl` 轨迹文件。适用于：

- 构造评估用例（eval case）
- 复现和调试 agent 行为
- 手动编排理想的工具调用序列

## 启动

```bash
# 确保数据库已启动（工具调用依赖 NovelStore）
# 启动 debug 服务
uv run python -m novel_debug
```

服务启动后访问 http://localhost:9004 。

> 默认端口 `9004`，可在 `__main__.py` 中修改。

## 使用指南

### 工具测试场

1. 在顶部 **书籍** 下拉框中选择目标书籍
2. 左侧 **工具列表** 点击要测试的工具
3. 填写参数表单（`book` 字段会自动注入，无需手动填写）
4. 点击 **执行**，查看返回结果

### 轨迹构建器

一个完整的轨迹构建流程如下：

1. **新建会话** — 点击左侧「新建会话」，输入用户问题，点击「开始」
2. **调用工具** — 在底部工具栏选择工具、填写参数，点击「调用」，工具结果会自动追加到时间线
3. **撤回** — 如果调用结果不理想，点击「撤回上一步」删除最后一次调用
4. **粘贴模型回答** — 将外部 LLM 的回答粘贴到文本框，点击「提交回答」
5. **复制轨迹** — 点击「复制轨迹」将人类可读的轨迹摘要复制到剪贴板，方便发给外部模型
6. **结束轮次** — 点击「结束轮次」写入 TurnEnd 并关闭会话

#### 历史会话

左侧边栏展示所有历史会话，点击可恢复查看时间线。会话数据存储在 `sessions/` 目录下，每个会话一个 `wire.jsonl` 文件。

## 数据文件

```
sessions/
  manual_20260515_180341/
    wire.jsonl          # 轨迹文件（JSONL 格式）
  manual_20260516_093000/
    wire.jsonl
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tools` | 获取工具列表及参数定义 |
| GET | `/api/books` | 获取已入库书籍列表 |
| POST | `/api/tools/{name}/call` | 调用指定工具 |
| GET | `/api/sessions` | 列出历史会话 |
| POST | `/api/sessions` | 创建新会话 |
| GET | `/api/sessions/{id}/timeline` | 获取会话时间线 |
| POST | `/api/sessions/{id}/tool-call` | 追加工具调用 |
| POST | `/api/sessions/{id}/undo` | 撤回最后一次调用 |
| POST | `/api/sessions/{id}/text` | 追加模型回答 |
| POST | `/api/sessions/{id}/end` | 结束轮次 |
| GET | `/api/sessions/{id}/wire` | 导出 wire.jsonl |
| GET | `/api/sessions/{id}/copy` | 获取可复制的轨迹摘要 |

## 模块结构

```
src/novel_debug/
  __init__.py       # 包声明
  __main__.py       # 入口：启动 uvicorn 服务
  app.py            # FastAPI 路由与中间件
  session.py        # 会话管理（创建/追加/撤回/导出）
  tools.py          # 工具封装层（绕过 agent 直接调用）
  wire_format.py    # wire.jsonl 消息格式生成
  static/
    index.html      # 页面结构
    app.js          # 前端交互逻辑
    style.css       # 样式
```
