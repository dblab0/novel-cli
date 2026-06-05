# Agent 开发指南

## 概述

Novel CLI 的 Agent 系统支持两种类型：
- **内置 Agent**：随 CLI 一起发布，当前仅有 `default`
- **外置 Agent**：通过 YAML 声明式配置，放置在 `agents/` 目录

## Agent 体系

### 内置 Agent

| Agent | 说明 |
|-------|------|
| default | 统一 Agent v3（含子智能体委托，11 个工具） |

```bash
novel-cli --agent default
```

### 外置 Agent

| Agent | 说明 |
|-------|------|
| novel-unified-v3 | 最新统一 Agent（主力版本） |
| novel-unified-v2 | 统一 Agent v2 |
| novel-unified-v1 | 统一 Agent v1 |
| novel-v2 / v3 / v4 | 专注型 Agent |
| novelskill-v1 / v1.1 / v2 | Skill 增强型 Agent |

```bash
novel-cli --agent-file agents/novel-unified-v3/agent.yaml
```

## novel-unified-v3（主力版本）

### 核心机制

#### 五步查询管线

v3 采用严格的五步查询管线，确保信息检索的准确性：

1. **实体搜索**（SearchEntity）— 查找相关实体
2. **图谱概览**（SearchGraph 无 rel_type）— 获取实体描述和关系概览
3. **图谱详情**（SearchGraph 有 rel_type）— 获取具体关系详情和章节引用
4. **语料检索**（SearchCorpus）— 在原文中检索关键段落
5. **章节精读**（ReadChapter）— 读取完整章节或句级范围

每一步的输出为下一步提供输入，形成完整的信息检索链。

#### 子智能体委托

v3 引入了子智能体委托机制，用于处理多实体任务：

```
准备（确认 entity_ids） → 分发（并行启动 novel-researcher） → 汇总（收集合并结果）
```

- **准备阶段**：通过 SearchEntity 确认各实体的 ID
- **分发阶段**：为每个实体并行启动 novel-researcher 子智能体
- **汇总阶段**：收集各子智能体的结果，合并为统一回答

仅当任务涉及多个实体时才触发委托。

#### novel-researcher 子智能体

专门用于单实体深度检索的子智能体：
- 继承 v3 Agent 配置
- 排除 Agent 和 AskUserQuestion 工具
- 执行完整的五步查询管线
- 专注于单一实体的信息收集

#### 防幻觉机制

v3 强制执行防幻觉规则：每条信息必须有检索来源。Agent 在回答时必须引用具体的工具调用结果，不得编造信息。

### 工具集（11 个）

| 工具 | 说明 |
|------|------|
| AskUserQuestion | 向用户提问 |
| ReadFile | 读取文件 |
| WriteFile | 写入文件 |
| StrReplaceFile | 字符串替换编辑文件 |
| Glob | 文件模式匹配 |
| Grep | 文件内容搜索 |
| SearchEntity | 实体搜索 |
| SearchGraph | 图谱查询 |
| SearchCorpus | 语料检索 |
| ReadChapter | 章节阅读 |
| Agent | 子智能体委托（v3 新增） |

### 工具验证器

- SearchGraph：`require_overview_before_detail: true` — 必须先查概览再查详情

## novel-unified-v2

v2 是 v3 的前代版本，核心差异：

| 维度 | v2 | v3 |
|------|----|----|
| 工具数量 | 10 | 11（新增 Agent） |
| 子智能体 | 不支持 | 支持 novel-researcher |
| 多实体处理 | 串行查询 | 并行委托 |
| 复杂任务能力 | 中等 | 高 |
| 适用场景 | 简单问答 | 复杂分析、多实体任务 |

v2 适合简单的问答场景，v3 适合需要深度分析和多实体对比的复杂任务。

## YAML Spec 开发

### 目录结构

```
agents/<name>/
├── agent.yaml    # Agent 规格文件
└── system.md     # System prompt
```

### agent.yaml 字段

```yaml
version: 1
agent:
  name: MyAgent
  system_prompt_path: ./system.md
  tools:
    - novel_cli.tools.ask_user:AskUserQuestion
    - novel_cli.tools.file:ReadFile
    - novel_cli.tools.novel:SearchEntity
    - novel_cli.tools.novel:SearchGraph
    - novel_cli.tools.novel:SearchCorpus
    - novel_cli.tools.novel:ReadChapter
  subagents:
    - name: my-researcher
      path: ./my-researcher.yaml
      description: "子智能体描述"
  tool_validators:
    - tool: SearchGraph
      require_overview_before_detail: true
```

### 关键字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| version | string | 版本号（支持值：`1`、`1.1`） |
| agent.name | string | Agent 名称 |
| agent.system_prompt_path | string | System prompt 文件路径 |
| agent.tools | list | 工具列表（模块路径:类名） |
| agent.subagents | list | 子智能体配置（每项含 name/path/description） |
| agent.tool_validators | list | 工具验证规则（每项含 tool/验证条件） |

### extend 继承

```yaml
version: 1
agent:
  extend: default  # 继承 default agent 的配置
  name: MyExtendedAgent
  tools:
    - novel_cli.tools.novel:SearchEntity  # 追加工具
```

> **注意**：专注型 Agent 禁止使用 `extend: default`。专注型 Agent 与 default Agent 职责不同，必须完全独立声明所有字段。

### 可用工具模块

| 模块路径 | 工具类 | 说明 |
|----------|--------|------|
| novel_cli.tools.ask_user | AskUserQuestion | 向用户提问 |
| novel_cli.tools.file | ReadFile | 读取文件 |
| novel_cli.tools.file | WriteFile | 写入文件 |
| novel_cli.tools.file | StrReplaceFile | 字符串替换 |
| novel_cli.tools.file | Glob | 文件匹配 |
| novel_cli.tools.file | Grep | 内容搜索 |
| novel_cli.tools.novel | SearchEntity | 实体搜索 |
| novel_cli.tools.novel | SearchGraph | 图谱查询 |
| novel_cli.tools.novel | SearchCorpus | 语料检索 |
| novel_cli.tools.novel | ReadChapter | 章节阅读 |
| novel_cli.tools.agent | Agent | 子智能体委托 |
