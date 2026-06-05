# novel-eval

Novel Agent 评估框架。支持两种评估任务：**工具使用评估**（tool_usage）和**技能生成评估**（skill_generation）。通过 LLM 自动生成测试用例，驱动 novel-cli 子进程执行，再由 Judge LLM 对工具调用质量进行多维度评分，并提供 Web 可视化界面。

## 安装

在 workspace 根目录执行：

```bash
# 安装为全局工具（推荐，提供 novel-eval 命令）
uv tool install --editable ./packages/novel_eval

# 或者仅在当前虚拟环境中使用
uv sync
python -m novel_eval --help
```

安装后即可使用 `novel-eval` 命令。更新代码后无需重新安装（editable 模式自动生效）。

## 快速开始

### 1. 准备配置

在工作目录创建 `eval_config.yaml`（也可以直接使用包内默认配置）：

```yaml
# 被评估的 agent LLM 配置
agent:
  model: DeepSeek-R1
  base_url: http://localhost:4000/v1
  api_key_env: EVAL_API_KEY    # 从 .env 文件读取

# Runner 配置（控制子进程如何调用 novel-cli）
runner:
  work_dir: ~/novel_test       # --work-dir 参数
  agent_file: agents/novel-v3/agent.yaml  # --agent-file 参数
  timeout: 600                 # 子进程超时秒数
  eval_model: minimax/minimax-m2.7  # 评估时 novel-cli 使用的模型

# Judge LLM 配置（独立）
judge:
  model: DeepSeek-R1
  base_url: http://localhost:4000/v1
  api_key_env: EVAL_API_KEY

# 设定文件目录
settings_dir: /path/to/settings

# 并发数
concurrency: 2
```

在项目根目录创建 `.env` 文件：

```
EVAL_API_KEY=sk-xxx
```

### 2. 三步完成评估（工具使用）

```bash
# 第一步：生成测试用例
novel-eval generate --book 西游记 --scenario level_1_entity

# 第二步：执行评估（显示两个进度条：执行评估 + Judge 评分）
novel-eval run eval_cases/西游记/level_1_entity.yaml

# 第三步：查看报告
cat eval_results/西游记/novel-v3/level_1_entity/2026-04-27_120000/report.md
```

### 3. 启动可视化界面

```bash
novel-eval view --eval-dir eval_results --port 8080
```

浏览器自动打开，即可按 书籍 → Agent → 场景 → 运行 浏览评估结果。

## 评估任务类型

novel-eval 支持两种评估任务，通过 `--task` 参数切换：

| 任务 | `--task` 值 | 用例生成方式 | 评分维度 | 用途 |
|------|-------------|-------------|---------|------|
| 工具使用评估 | `tool_usage`（默认） | LLM + 设定文件 | 4 维度 | 评估 agent 工具调用的准确性 |
| 技能生成评估 | `skill_generation` | CSV 实体数据抽取 | 5 维度 | 评估 agent 生成设定的能力 |

---

## 任务一：工具使用评估（tool_usage）

评估 agent 调用工具（entity / graph / corpus）的准确性，包括工具选择、参数构造、调用效率等。

### 生成用例

```bash
novel-eval generate --task tool_usage --book 西游记 --scenario level_1_entity
```

**难度等级：**

| 等级 | 名称 | 考察点 | 工具类型 | 示例问题 |
|------|------|--------|----------|----------|
| 1 | 单次调用 | 基础工具选择 | `entity` | "韩立是谁？" → entity |
| 2 | 两步链式 | 参数传递 | `entity`, `graph` | "韩立的师父是谁？" → entity → graph |
| 3 | 多步链式 | 关系验证 | `entity`, `graph`, `corpus` | "韩立与玄骨上人合作的具体场景原文是怎样的？" |
| 4 | 综合推理 | 信息整合 | `entity`, `graph`, `corpus` | "比较韩立在血色禁地与虚天殿两次探险中的战斗手段" |

**工具类型说明：**

| 工具类型 | 用途 | 适用等级 |
|----------|------|----------|
| `entity` | 搜索实体（人物、法宝、门派等） | Level 1-4 |
| `graph` | 查询知识图谱关系（人物关系、物品归属等） | Level 2-4 |
| `corpus` | 查找原文（验证细节、对比描写、提取场景） | Level 3-4 |

- Level 2 不涉及 `corpus`，保持 `entity → graph` 的简单链式结构
- Level 3 的 `corpus` 用于验证关系细节
- Level 4 的 `corpus` 用于信息整合

**使用 `--instruction` 控制生成方向：**

```bash
novel-eval generate --book 西游记 --scenario level_1_entity \
  --instruction "生成问题集中在前500章的剧情"

novel-eval generate -b 西游记 -s level_2_chain \
  -i "围绕门派势力分布和灵根体系生成问题"
```

**使用 `--append` 追加用例：**

```bash
novel-eval generate --book 西游记 --scenario level_1_entity --append
```

### 执行评估

```bash
# 正常模式：完整流程
novel-eval run eval_cases/西游记/level_1_entity.yaml

# 批量执行（多文件）
novel-eval run eval_cases/西游记/level_*.yaml

# skip-run 模式：跳过执行，仅重新 Judge 评分
novel-eval run --skip-run \
  eval_cases/西游记/level_1_entity.yaml \
  --result-dir eval_results/西游记/novel-v3/level_1_entity/2026-04-27_120000/

# rerun-missing 模式：补跑缺失用例
novel-eval run --rerun-missing \
  eval_cases/西游记/level_1_entity.yaml \
  --result-dir eval_results/西游记/novel-v3/level_1_entity/2026-04-27_120000/
```

### 评分维度（4 维度，每项 1-5 分）

| 维度 | 说明 |
|------|------|
| `tool_selection` | 工具选择是否合理 |
| `param_quality` | 传入参数是否精准 |
| `call_efficiency` | 调用链有无冗余或遗漏 |
| `result_utilization` | 是否充分利用工具返回 |

**关键扣分项：**
- 使用多关键词参数而非精确查询
- 单次查询超过 3 个关键词
- 重复调用同一工具查询相同内容

---

## 任务二：技能生成评估（skill_generation）

评估 agent 生成小说设定（角色/物品/地点/组织/功法）的能力。用例直接从 CSV 实体数据中抽取高频实体，无需 LLM 生成。

### 支持的实体类型

| 实体类型 | skill_type | 输出目录 | ID 前缀 |
|----------|-----------|---------|---------|
| 人物 | `generate-character` | `characters/` | `CHAR` |
| 物品 | `generate-item` | `items/` | `ITEM` |
| 地点 | `generate-location` | `locations/` | `LOC` |
| 组织 | `generate-organization` | `organizations/` | `ORG` |
| 技能 | `generate-skill` | `skills/` | `SKILL` |

### 生成用例

```bash
# 从 CSV 数据抽取实体，按类型生成测试用例
novel-eval generate --task skill_generation --book 西游记

# 指定每种类型抽取数量和随机种子（确保可复现）
novel-eval generate --task skill_generation --book 西游记 --samples 10 --seed 42

# 追加模式
novel-eval generate --task skill_generation --book 西游记 --append
```

用例生成后会按实体类型拆分为多个 YAML 文件：

```
eval_cases/
└── 西游记/
    ├── skill_generation_characters.yaml
    ├── skill_generation_items.yaml
    ├── skill_generation_locations.yaml
    ├── skill_generation_organizations.yaml
    └── skill_generation_skills.yaml
```

### 执行评估

```bash
# 正常模式
novel-eval run --task skill_generation \
  eval_cases/西游记/skill_gen_characters.yaml

# 批量执行所有类型
novel-eval run --task skill_generation \
  eval_cases/西游记/skill_gen_*.yaml

# skip-run 模式：仅重新评分
novel-eval run --task skill_generation --skip-run \
  eval_cases/西游记/skill_gen_characters.yaml \
  --result-dir eval_results/西游记/novel-v3/skill_generation_characters/2026-04-27_120000/
```

### 评分维度（5 维度，每项 1-5 分）

| 维度 | 说明 |
|------|------|
| `tool_selection` | 工具选择是否合理 |
| `param_quality` | 参数构造是否精准 |
| `call_efficiency` | 调用链是否高效 |
| `setting_completeness` | 设定文档的完整性 |
| `format_compliance` | 格式合规性（对比 SKILL.md 模板） |

评分时会自动加载对应的 `SKILL.md` 模板作为格式和完整性的参考依据。

---

## CLI 命令详解

### `novel-eval generate` — 生成测试用例

```bash
novel-eval generate [OPTIONS]
```

| 参数 | 缩写 | 默认值 | 说明 |
|------|------|--------|------|
| `--config` | `-c` | 包内默认 | 配置文件路径 |
| `--task` | `-t` | `tool_usage` | 任务类型（`tool_usage` / `skill_generation`） |
| `--book` | `-b` | `西游记` | 书名 |
| `--output` | `-o` | `eval_cases` | 输出目录 |
| `--scenario` | `-s` | `level_1_entity` | 场景名称（tool_usage 使用） |
| `--eval-model` | `-m` | 配置文件值 | 评估模型，覆盖配置 |
| `--instruction` | `-i` | 无 | 自定义指令，引导生成方向（tool_usage 使用） |
| `--seed` | | 无 | 随机种子，确保可复现 |
| `--samples` | | 无 | 每种类型抽取数量（skill_generation 使用） |
| `--append` | `-a` | 否 | 追加模式，向已有文件追加用例 |

### `novel-eval run` — 执行评估

```bash
novel-eval run [OPTIONS] [YAMLS...]
```

| 参数 | 缩写 | 默认值 | 说明 |
|------|------|--------|------|
| `--config` | `-c` | 包内默认 | 配置文件路径 |
| `--task` | `-t` | `tool_usage` | 任务类型 |
| `YAMLS` | | **必填** | YAML 用例文件路径（位置参数），支持多文件 |
| `--output` | `-o` | `eval_results` | 结果输出根目录 |
| `--skip-run` | | 否 | 跳过 agent 执行，仅进行 Judge 评分 |
| `--result-dir` | `-r` | 无 | 已有评估结果目录（skip-run/rerun-missing 模式必填） |
| `--rerun-missing` | | 否 | 重跑缺失用例，自动检测并重跑 |

**执行流程与进度显示：**

```
执行评估: 100%|████████████████| 10/10 [02:30<00:00, 15.0s/用例]  ← agent 执行阶段
Judge 评分: 100%|████████████████| 10/10 [00:15<00:00, 1.5s/用例] ← 评分阶段
```

**三种运行模式对比：**

| 模式 | 用途 | 命令示例 |
|------|------|---------|
| 正常模式 | 完整执行 + 评分 | `novel-eval run cases.yaml` |
| skip-run | 仅重新评分 | `novel-eval run --skip-run cases.yaml -r <result_dir>` |
| rerun-missing | 补跑缺失用例 | `novel-eval run --rerun-missing cases.yaml -r <result_dir>` |

### `novel-eval extract-context` — 提取上下文消息

从 session 目录提取对话记录（自动在正常模式 run 中执行，也可单独调用）：

```bash
novel-eval extract-context \
  --result-dir eval_results/西游记/novel-v3/level_1_entity/2026-04-27_120000/
```

会在每个用例目录下生成 `messages.jsonl` 和 `messages.md`，记录完整的对话内容（过滤内部状态）。

### `novel-eval regen-report` — 重新生成报告

从已有 `cases/` 目录重新生成报告，无需重跑评估：

```bash
novel-eval regen-report \
  --result-dir eval_results/西游记/novel-v3/level_1_entity/2026-04-27_120000/
```

### `novel-eval view` — 启动 Web 可视化界面

```bash
novel-eval view [OPTIONS]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port` | `8080` | 服务端口 |
| `--host` | `127.0.0.1` | 监听地址（用 `0.0.0.0` 允许外部访问） |
| `--eval-dir` | `./eval_results` | 评估结果目录 |
| `--no-open` | 否 | 不自动打开浏览器 |
| `--dev` | 否 | 开发模式（跳过静态文件挂载，前端由独立 dev server 提供） |

**功能特性：**
- 按 书籍 → Agent → 场景 → 运行 维度浏览评估结果
- 单次运行报告查看（含用例详情和消息记录）
- 多 Agent 对比分析（`/api/compare`）
- 时间趋势和回归检测（`/api/trend`）
- 索引热刷新（`POST /api/reindex`）

**使用示例：**

```bash
# 默认启动
novel-eval view

# 指定数据和端口
novel-eval view --eval-dir ./eval_results --port 3000

# 允许外部访问
novel-eval view --host 0.0.0.0 --port 8080

# 开发模式（前后端分离）
novel-eval view --dev
```

**REST API 端点：**

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/books` | 列出所有书籍 |
| GET | `/api/books/{book}/agents` | 列出 Agent |
| GET | `/api/books/{book}/agents/{agent}/scenarios` | 列出场景 |
| GET | `/api/books/{book}/agents/{agent}/scenarios/{scenario}/runs` | 列出运行批次 |
| GET | `/api/runs/{run_id}/report` | 获取报告 |
| GET | `/api/runs/{run_id}/cases` | 列出用例摘要 |
| GET | `/api/runs/{run_id}/cases/{case_id}` | 获取用例详情 |
| GET | `/api/runs/{run_id}/cases/{case_id}/messages` | 获取消息记录 |
| GET | `/api/compare?book=...&agents=...&scenario=...` | 多 Agent 对比 |
| GET | `/api/trend?book=...&agent=...&scenario=...` | 时间趋势 |
| POST | `/api/reindex` | 重建索引 |

---

## 输出目录结构

### 用例目录

```
eval_cases/
└── <书名>/
    ├── level_1_entity.yaml          # tool_usage 用例
    ├── level_2_chain.yaml
    ├── level_3_multi_step.yaml
    ├── level_4_complex.yaml
    ├── skill_generation_characters.yaml  # skill_generation 用例
    ├── skill_generation_items.yaml
    ├── skill_generation_locations.yaml
    ├── skill_generation_organizations.yaml
    └── skill_generation_skills.yaml
```

### 结果目录

输出路径格式：`eval_results/<书名>/<agent版本>/<场景>/<模型>_<时间戳>/`

其中 `<agent版本>` 从 `eval_config.yaml` 中的 `runner.agent_file` 自动提取（取父目录名），如 `agents/novel-v3/agent.yaml` → `novel-v3`。

```
eval_results/
└── 西游记/
    ├── novel-v2/
    │   └── level_1_entity/
    │       └── 2026-04-26_120000/
    │           ├── report.json              # 结构化数据（含工具统计）
    │           ├── report.md                # 可读报告
    │           └── cases/
    │               ├── L1-001.json          # 单用例详细记录
    │               ├── L1-001/
    │               │   └── messages.md      # 对话记录
    │               └── L1-002.json
    └── novel-v3/
        ├── level_1_entity/
        │   └── minimax-m2.7_2026-04-29_173607/
        ├── skill_generation_characters/
        │   └── minimax-m2.7_2026-04-30_100000/
        └── ...
```

**report.json 结构：**

```json
{
  "summary": { "total": 10, "avg_score": 3.8, "pass_rate": 0.7 },
  "tool_stats": {
    "global": { "total_calls": 25, "unique_calls": 20, "duplicate_calls": 5 },
    "by_level": { ... },
    "cases_summary": [ ... ]
  },
  "cases": [ ... ]
}
```

**cases/*.json 结构：**

每个单用例 JSON 文件包含：
- `tool_calls`：工具调用记录（tool_name、params、结果摘要）
- `final_answer`：最终回答
- `execution_time`：执行耗时
- `score`：多维度评分及评语

## YAML 用例格式

### tool_usage 用例

```yaml
book: 西游记
level: 1
description: Level 1 测试用例
cases:
  - id: L1-001
    model: minimax/minimax-m2.7
    question: "韩立的主修功法是什么？"
    meta:
      involved_entities:
        - 青元剑诀
      expected_tool_types:
        - entity
    _validation: null
```

### skill_generation 用例

```yaml
book: 西游记
cases:
  - id: CHAR-001
    question: "请为「韩立」生成完整的角色设定"
    meta:
      entity_name: 韩立
      entity_type: 人物
      skill_type: generate-character
```

也可以手动编辑 YAML 文件，添加或修改测试问题后执行评估。

## 配置项说明

```yaml
# 被评估的 agent LLM 配置（OpenAI 兼容协议）
agent:
  model: DeepSeek-R1           # 模型名称
  base_url: http://...:4000/v1 # API 地址
  api_key_env: EVAL_API_KEY    # 环境变量名（也可用 api_key 直接填写）

# Runner 子进程配置
runner:
  work_dir: ~/novel_test       # novel-cli 工作目录
  agent_file: agents/novel-v3/agent.yaml  # --agent-file 参数（路径父目录名作为输出路径中的版本标识）
  timeout: 600                 # 单用例超时秒数
  eval_model: minimax/minimax-m2.7  # 评估时 novel-cli 使用的模型（写入用例 YAML）

# Judge LLM 配置（独立于 agent）
judge:
  model: DeepSeek-R1
  base_url: http://...:4000/v1
  api_key_env: EVAL_API_KEY

# 设定文件根目录（下面按书名分子目录）
settings_dir: /path/to/settings

# 并发执行数
concurrency: 2

# 任务配置
tasks:
  tool_usage:
    books:
      - name: 西游记
        scenarios: [level_1_entity, level_2_chain, level_3_multi_step, level_4_complex]
  skill_generation:
    data_dir: data/input_data     # CSV 实体数据目录
    min_descriptions: 20          # 最小描述条目数阈值
    samples_per_type: 10          # 每种类型抽取数量
```

## 设定文件目录要求

`settings_dir` 下的目录结构应为：

```
settings/
└── 西游记/
    ├── world-final.md          # 世界观设定
    ├── power_system-final.md   # 修炼体系
    └── currency-final.md       # 货币/资源体系
```

框架按优先级读取 `world-final.md`、`power_system-final.md`、`currency-final.md` 三个文件，合并后作为生成问题的上下文。

## 架构概览

```
novel_eval/
├── __init__.py
├── __main__.py           # python -m 入口
├── cli.py                # CLI 命令（generate / run / extract-context / regen-report / view）
├── config.py             # 配置加载（YAML + 环境变量替换）
├── eval_config.yaml      # 默认配置文件（位于项目根目录）
├── models.py             # 核心数据模型（EvalCase / ToolCallRecord / CaseResult / EvalScore）
├── runner.py             # 子进程运行器（novel-cli 调用 + wire.jsonl 解析）
├── report.py             # 报告生成器（JSON + Markdown + 三级工具统计）
├── context.py            # 上下文消息提取器
├── prompts/              # Jinja2 模板
│   ├── loader.py         # 模板加载器
│   ├── generator_system.md.j2
│   ├── generator_user.md.j2
│   ├── judge_system.md.j2
│   ├── judge_user.md.j2
│   ├── skill_gen_judge_system.md.j2
│   └── skill_gen_judge_user.md.j2
├── tasks/
│   ├── base.py           # 评估任务抽象基类
│   ├── tool_usage/       # 工具使用评估任务（4 维度评分）
│   │   ├── models.py     # 配置模型
│   │   ├── generator.py  # LLM 问题生成器
│   │   └── judge.py      # LLM Judge 评分器
│   └── skill_generation/ # 技能生成评估任务（5 维度评分）
│       ├── models.py     # 实体类型映射表
│       ├── task.py       # 任务实现
│       ├── generator.py  # CSV 数据抽取器
│       └── judge.py      # 5 维度评分器（注入 SKILL.md 模板）
└── viewer/               # Web 可视化界面
    ├── server.py         # FastAPI 服务器 + SPA 静态文件
    └── data.py           # 数据索引（目录扫描 + 报告加载）
```

**数据流：**

```
eval_config.yaml
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  generate 命令                                          │
│                                                         │
│  tool_usage:     设定文件 → Jinja2 + LLM → YAML 用例    │
│  skill_generation: CSV 数据 → 实体抽取 → YAML 用例      │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  run 命令（正常模式）                                    │
│                                                         │
│  YAML 用例                                              │
│      → Runner（novel-cli 子进程）→ wire.jsonl 解析       │
│      → Judge LLM（多维度评分）                          │
│      → 报告生成（report.json + report.md + cases/*.json）│
│      → 上下文提取（messages.jsonl + messages.md）        │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  view 命令                                              │
│                                                         │
│  eval_results/ → EvalDataIndex 索引 → REST API + SPA    │
│  支持多 Agent 对比、时间趋势、回归检测                   │
└─────────────────────────────────────────────────────────┘
```

## Python API

除了 CLI，也可以通过 Python API 调用：

### 工具使用评估

```python
from pathlib import Path
from novel_eval.config import load_config
from novel_eval.tasks.tool_usage import ToolUsageTask
from novel_eval.models import load_scored_results_from_dir

# 加载配置
cfg = load_config(Path("eval_config.yaml"))
task = ToolUsageTask(cfg)

# 生成用例
cases = task.generate_cases(
    book="西游记",
    settings_dir=Path(cfg.settings_dir),
    scenario="level_1_entity",
    eval_model=cfg.runner.eval_model,
    instruction="集中在法宝和丹药",
)
task.save_cases(Path("eval_cases"), cases, "西游记", scenario="level_1_entity")

# 执行完整评估
results = await task.evaluate(cases)

# skip-run 模式
existing_results = load_scored_results_from_dir(
    Path("eval_results/西游记/novel-v3/level_1_entity/2026-04-27_120000/")
)
results = await task.evaluate([], skip_run=True, existing_results=existing_results)
```

### 技能生成评估

```python
from novel_eval.tasks.skill_generation import SkillGenerationTask

task = SkillGenerationTask(cfg)

# 从 CSV 数据生成用例
cases = task.generate_cases(
    book="西游记",
    seed=42,
    samples_per_type=10,
)

# 保存（按实体类型拆分为多个文件）
paths = task.save_cases(Path("eval_cases"), cases, "西游记")

# 执行评估
results = await task.evaluate(cases)
```
