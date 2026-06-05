---
name: agent-eval-analyzer
description: |
  分析 Agent 评估结果，定位优化方向，输出结构化问题清单。

  当用户提到以下场景时务必使用此 skill：
  - "分析 agent 评估结果"、"agent 跑分不好"、"看看 agent 哪里可以优化"
  - "评估 case 分析"、"eval 结果分析"、"judge 打分分析"
  - "agent 调用轨迹分析"、"工具调用效率低"
  - 用户提供了 eval_results 路径，想要理解 agent 在哪些 case 上表现不佳
  - 用户想要对比 agent 的表现并找出改进空间

  即使用户只是说 "帮我看看 agent 跑得怎么样" 或 "eval 结果帮我分析一下"，也应触发此 skill。
---

# Agent Eval Analyzer

分析 agent 的评估调用轨迹，从工具选择、参数质量、调用效率、结果利用四个维度定位优化方向，输出结构化问题清单到 `docs/agent-issues/`。

## 执行流程

### 第一步：收集信息

向用户确认以下信息（如果用户已提供则跳过对应项）：

```
1. Agent 名称：要分析的 agent 名称（对应 agents/<name>/ 目录，如 novel-v3, novelskill-v1）
2. 重点工具：核心工具所在目录（默认 `src/novel_cli/tools/novel/`，含 SearchEntity、SearchGraph、SearchCorpus 三个工具）
3. 评估结果：eval 结果路径（通常在 eval_results/<书名>/<agent_name>/<task_type>/<时间戳>/）
4. Judge 打分：如已有 judge 打分且可解析，请告知（如未解析或解析失败则不得参考）
```

使用 AskUserQuestion 逐项确认缺失的信息，一次性问完不要分多次。

**目录结构参考**：

Agent 配置统一放在项目根目录 `agents/`，每个 agent 子目录包含：
- `agent.yaml` — 工具列表、system prompt 路径、版本
- `system.md` — 完整的 system prompt

评估结果按层级组织：
```
eval_results/
└── <书名>/                    # 如 "凡人修仙传"
    ├── novel-v3/              # agent 名称
    │   ├── level_1_entity/    # 任务类型
    │   │   └── <model>_<时间戳>/
    │   │       ├── cases/     # 具体评估 case
    │   │       ├── report.json
    │   │       └── report.md
    │   ├── level_2_chain/
    │   ├── level_3_multi_step/
    │   └── level_4_complex/
    ├── novel-v4/
    │   └── ...（同上结构）
    └── novelskill-v1/
        ├── skill_gen_character/    # skill_generation 任务
        ├── skill_gen_item/
        ├── skill_gen_location/
        ├── skill_gen_organization/
        └── skill_gen_skill/
```

当用户只提到"分析一下 eval 结果"而未指定具体 agent 或路径时，先扫描 `eval_results/` 和 `agents/` 目录列出可选项供用户选择。

### 第二步：读取 Agent 配置

根据用户指定的 agent 名称，读取 `agents/<name>/` 目录下的文件：
- `agents/<name>/agent.yaml` — 工具列表、system prompt 路径、版本
- `agents/<name>/system.md` — 完整的 system prompt，重点关注：
  - 工具使用决策树
  - 搜索策略引导（如"优先 name 模式"、"一个关键词"等规则）
  - Failure recovery 指导
  - 验证/确认流程的指导（是否要求总是做 corpus 验证等）

### 第三步：读取工具实现

读取用户指定的重点工具目录下的所有实现文件，理解：
- 每个工具的参数定义（字段、默认值、约束）
- 工具的输出格式化逻辑（formatter）
- 工具的描述文档（.md 文件，作为 LLM 看到的 tool description）
- 工具的错误处理和提示信息

### 第四步：分析评估轨迹

对每个 eval case：

1. **提取工具调用链**：按顺序记录每一步调用的工具名、关键参数、返回结果摘要
2. **识别关键模式**：
   - 冗余调用：在已有充足信息时仍继续调用（如 entity + graph 已回答问题但仍查 corpus 验证）
   - 无效调用：参数不合理导致返回空结果（如多关键词拼凑、不存在的实体名）
   - 过度探索：有多个候选时逐一深入而非优先探索最可能的
   - 策略偏差：搜索模式选择不当（应先 name 却先 vector，或反过来）
3. **对照 system.md 规则**：检查 agent 是否违反了自己 system prompt 中的规则
4. **记录得分**：如 judge 打分可解析，记录各维度分数

### 第五步：归因分析

对识别出的每个问题，分析根因并归类：

- **System Prompt 问题**：规则过于笼统、缺少关键引导、存在矛盾的指令
- **工具输出问题**：输出信息不足导致 agent 需要额外调用（如 related 不带描述、types 不带数量）
- **工具参数问题**：参数设计不够约束或过于灵活导致误用
- **Agent 推理问题**：LLM 本身的推理偏差（如不从已知实体出发而是猜测名字）

### 第六步：输出问题清单

将分析结果写入 `docs/agent-issues/<eval_name>.md`，其中 `<eval_name>` 取自评估目录名（如 `level_1_entity`）。

输出格式：

```markdown
# <Agent 名> 优化问题清单

> 基于 `<eval_results 路径>` N 个 case 的 judge 评估结果分析。
> 评估维度：工具选择(X.XX) / 参数质量(X.XX) / 调用效率(X.XX) / 结果利用(X.XX)，总分 X.XX/5。

---

## 问题一：<简短描述>

**优先级**: P0/P1/P2
**影响 case**: L1-XXX, L1-XXX
**相关文件**: `<具体文件路径:行号>`

### 现象
（具体描述 agent 做了什么，引用实际的工具调用步骤）

### 根因
（分析为什么会发生，引用具体的 system.md 规则或 formatter 代码）

### 建议方案
（具体的修改建议，包含期望的改动前/后效果）

---

（重复上述结构，每个问题一个章节）

---

## 附录：各 Case 得分与工具调用摘要

| Case | 问题 | 调用步数 | 总分 | 主要问题 |
|------|------|----------|------|----------|
| ... | ... | ... | ... | ... |

### 最优流程模板（来自最高分 case）
（记录满分 case 的调用链，作为标杆）
```

### 优先级定义

| 优先级 | 含义 |
|--------|------|
| P0 | 高频问题，影响多个 case，改动小收益大（通常是 system.md 或 formatter 的小调整） |
| P1 | 中频问题，影响 1-2 个 case，需要一定改动（如工具输出增加字段） |
| P2 | 低频问题，偶发或边界情况，改动成本较高 |

### 注意事项

- 问题清单聚焦于**可操作的优化点**，不做泛泛的评价
- 每个问题必须关联到具体的 case 编号和文件路径
- 建议方案要具体到改哪个文件的哪段代码/文本
- 如果 judge 打分未解析或解析失败，明确标注"不得参考"，只从调用轨迹本身分析
- 调用效率维度通常是最大优化空间，重点关注
