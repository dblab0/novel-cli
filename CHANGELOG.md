# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

## [v1.0.0] - 2026-05-25

### Features

- SearchGraph 拦截显式传入 rel_type=null，返回可用关系类型提示
- eval 评估系统优化、viewer 增强、新增外置 agent 及工具验证器
- SearchCorpus 超长内容改为报错，与 SearchGraph 对齐
- 支持按类型配置描述阈值及多项优化
- 支持 DeepSeek V4 Flash 思考型模型参数配置
- 补充 cases_summary 缺少的 question 和 execution_time 字段
- 添加 install-novel-eval.sh 安装脚本
- 对话记录改为 JSONL 结构化渲染，支持折叠展示和重复调用标记

### Bug Fixes

- 修复 eval 对话记录缺少工具调用参数，优化工具链导航提示
- 加强 skill_gen judge 评估标准，修复 skip-run 模式 bug
- SearchCorpus 多章节结果增加章节分隔标识
- 同步 test_search_novel 过时测试到当前 API
- 更新 eval_config.yaml 配置

### Refactors

- SearchGraph 去掉 sub_action，改为 rel_type 隐式分发
- 移除 SearchEntity 的 entity_type 参数
- 重构 5 个设定生成模板，去状态化纯设定输出

### Docs

- 更新 novel-eval README 补充 skill_generation 任务文档
- 补充 eval-viewer-tasktype-removal 设计文档与任务初始化修复
- 归档 eval-task-unification change 到 openspec/archive
- 新增 agent 架构与问题分析文档

### Chores

-

---

## [v0.5.0] - 2026-05-25

### Features

- Web 模式支持 CLI 参数传递、书籍选择和会话配置
- 会话内 /book 命令支持内联书籍选择器
- 替换项目 logo 为 Novel CLI 专属设计
- 新增 novel_debug 调试模块和工具调用脚本
- 新增西游记评估用例
- import 命令默认启用 --clean 模式，导入前自动清除旧数据
- 工具测试场改为统一三面板视图
- 新增会话删除功能及工具参数自动修正，搜索结果补充章节号
- 新增 chapter 子命令，corpus 改为单数 keyword
- 工具测试场新增 Chapter 面板，撤回支持模型回答和轮次结束
- 新增 ReadChapter 工具，SearchCorpus 改为单数 keyword
- 轨迹构建器工具调用后实时更新侧边栏调用次数
- SearchGraph 支持逗号分隔多关系类型查询
- 支持内置 skill 输入和预测下一步工具调用

### Bug Fixes

- 修复 create-session-dialog 中的 import 顺序
- 修复 entities alias 导入丢失问题
- 修复 wire.jsonl 粘行解析崩溃，移除轨迹截断逻辑
- SearchGraph types 查询补充关系类型描述
- 修复轨迹构建器撤回残留、空行和剪贴板兼容问题
- 新建会话后立即刷新侧边栏会话列表
- 清理 work_dir 时保留设定目录，切换评估配置至 v1.1

### Refactors

- 重构导入脚本，修复 alias 解析并优化表结构
- 移除 source_id 依赖，改用 book 字段直接查询

### Docs

- 补充数据库启动说明、docker 项目名配置及设计文档

### Chores

-

---

## [v0.4.0] - 2026-05-25

### Features

- SearchGraph 关系详情显示目标实体别名
- 新增 novelskill-v1.1 agent
- 优化 novelskill v1/v2 system.md
- 统一评估任务架构 - dimensions 动态化 + registry 注册表
- 新增 skill_generation 评估任务与相关组件
- 优化 UX 细节与对比功能
- Case 详情 URL 改用 run_id + case_id 双参数路由
- 新增 Eval Web Viewer：可视化评测结果的全栈 Web 应用
- 增强 Novel 工具导航提示，优化 Agent 使用体验
- 新增 eval context 提取功能
- eval 结果目录路径加入模型名前缀

### Bug Fixes

- 优化 SearchCorpus 空结果恢复提示
- 优化 SearchCorpus 关键词策略
- 移除 generated_setting 机制，修复 skip-run 模式评分覆写问题
- skill_generation 任务补上 context 提取，输出 messages.md/jsonl
- 修复 eval report 生成 markdown 丢失 text 内容的问题
- 移除 eval 报告中的文本截断，Judge 解析失败时保留原始响应
- 修复测试中 ToolCallRecord.action → tool_name 和 EvalCase.model 参数错误

### Refactors

- eval 框架：levels 重命名为 scenarios，支持扩展任务类型
- 精简 Agent v4 system prompt，更新 agent 配置和 eval 参数
- Kosong 包大规模重构：精简类型注解、优化 API Provider 实现

### Docs

- 更新项目 Wiki 文档：新增 eval viewer 等模块文档
- 更新项目 Wiki 文档
- 新增 Agent 评测分析文档和 Kosong 升级设计方案
- 更新 zread wiki 文档至版本 2026-04-29-215335

### Chores

- 更新 zread wiki 文档至版本 2026-04-29-215335
- eval 结果目录路径加入模型名前缀
- 更新项目 Wiki 文档
- 更新项目 Wiki 文档：新增 eval viewer 等模块文档

---

## [v0.3.0] - 2026-05-25

### Features

- 增强 eval 报告工具调用统计：新增全局/分层/用例三级分析 + regen-report 命令
- 强化 novel agent 工具使用指引：强制 3 步查询管线 + chapter_ids 智能提示
- 新增 novel-v1.1 内置 agent 并优化搜索工具说明
- 新增 novel_eval 评估框架：工具使用评估全流程实现

### Bug Fixes

- 修复 novel_eval 解析 wire.jsonl 时 ToolCallPart 碎片丢失导致 params 为空
- 修复 novel_eval Judge 进度条更新不及时和 task 泄漏问题

### Refactors

- eval run 命令支持多 YAML 文件：将 --yaml 改为位置参数
- 移除 eval case YAML 中的 model 字段，运行时统一从 eval_config.yaml 读取
- eval 输出路径新增 agent 版本层 + 配置文件外置到项目根目录
- 迁移 novel agent 从内置到外置：使用 --agent-file 加载
- 重构 eval prompt 为模块化 Jinja2 模板：Judge/Generator 支持 system/user 双消息架构
- 重构 ToolCallRecord: action 字段改为 tool_name，skip-run 不再依赖 result_dir JSON
- 重构 SearchNovel 拆分为 SearchEntity/SearchGraph/SearchCorpus 三个独立工具
- 改造 SearchNovel 工具激活机制：从全局配置推模型改为 agent 声明拉模型
- 精简 novel agent：移除 shell/web/plan 等工具，聚焦小说知识库问答

### Docs

- 补充 novel_eval rerun-missing 模式文档，更新默认评估模型

### Chores

- 新增 GitHub Actions 配置、测试迁移设计文档和辅助脚本

---

## [v0.2.0] - 2026-05-25

### Features

- 新增 /book 命令和 SearchNovel 动态注入机制
- 实现 setup 命令模型能力配置功能
- 完善 PostgreSQL 统一存储：支持 halfvec 高维向量、corpus 导入与检索测试
- 实现 SearchNovel 工具：统一小说知识库查询

### Bug Fixes

- 修复 tests/tools 测试：支持中文环境与国内网络
- 修复 vis 测试环境变量名：KIMI_SHARE_DIR → NOVEL_SHARE_DIR
- 修复测试兼容中文语言环境
- 修复 SearchNovel 工具依赖注入问题：移除不必要的 future annotations

### Refactors

- 重构测试目录：将 novel 测试移至 tests/tools/novel/
- 迁移至 PostgreSQL 统一存储：替代 Milvus + Neo4j 三数据库架构

### Docs

- 新增 zread wiki 文档和 --book 参数设计文档
- 添加代码注释规范与中文化设计方案

### Chores

- 修复 ACP 测试卡住问题：恢复 acp_main 函数并修复认证配置
- 更新 acp 测试：项目改名 kimi → novel-cli
- 更新 utils 测试 snapshot：新增 novel 工具模块
- 更新 acp 测试：项目改名 kimi → novel-cli

---

## [v0.1.0] - 2026-05-25

### Features

- 从 Kimi CLI 移植核心代码并移除 OAuth 登录功能
- 添加 OpenRouter provider、示例代码和测试
- 添加手动 Agent 示例、调试日志和文档更新

### Bug Fixes

- 修复 question_panel：恢复被误删的 4 个 property return 语句
- 修复 kosong 错误消息：恢复中文错误信息并同步更新测试
- 修复 OpenRouter reasoning_details 提取逻辑并更新文档

### Refactors

- 重构 setup 向导确认页面：支持精确修改各配置部分
- 重构 setup 向导：支持已有 Provider 优先展示和多模型添加
- 重构 setup 命令为完整的 5 步引导式配置向导

### Docs

- 完善 README.md 文档：安装指南、命令使用说明和常见问题

### Chores

- 更新 hooks/tools/ui/utils 测试：适配项目改名和注释中文化
- 更新 e2e 测试：适配项目改名和注释中文化
- 更新 core 测试：kimi → novel 改名及注释中文化
- 更新 acp 测试：适配项目改名和注释中文化
- 更新 kosong anthropic provider 代码注释
- 更新项目配置：rules 规则、.gitignore 和 README
- 更新测试框架：tests_ai 与 tests_e2e 改名 kimi → novel
- 注释中文化：为所有模块添加中文注释和 docstring
- 添加前端 package-lock.json 文件
- 从 kimi-cli 迁移测试与前端目录

---

## [Unreleased]

---

## [v1.1.0] - 2026-06-04

### Features

- 新增 novel-unified-v3 agent 配置与子智能体（novel-researcher）定义
- 将 novel-unified-v3 内置为默认 agent，移除 okabe agent
- Web CLI 新增 `--agent` 参数，支持选择内置/外置 agent
- 新增工具调用循环检测与阻断机制，防止 agent 无限循环
- 新增批量 Skill 设定生成脚本 batch_skill_gen.py

### Bug Fixes

- 已知 entity_id 时跳过 SearchEntity 直接走 Graph 概览
- 移除 plan mode 全部 UI 元素
- 优化 batch_skill_gen.py 多项实际问题
- 修复 ReadChapter Step 5 参数格式：sentence_range 改为 start/end
- 同步 default/v2/v3 agent 配置与提示词，确保一致性

### Refactors

- 同步 novel-unified-v3 与 default agent system.md 完全一致
- 同步 novel-unified-v2 system.md 与 default/novel-researcher.md 一致

### Docs

- 补充 v3 设计、循环检测、知乎文章等设计文档
- 更新 wiki 文档体系，补充模块交互与 agent 规格细节

### Chores

- 更新 wiki 文档文件名映射与批量生成脚本并发参数

---

<!-- 链接 -->

[v1.1.0]: https://github.com/your-repo/novel-cli/compare/v1.0.0...v1.1.0

[v1.0.0]: https://github.com/your-repo/novel-cli/compare/v0.5.0...v1.0.0

[v0.5.0]: https://github.com/your-repo/novel-cli/compare/v0.4.0...v0.5.0

[v0.4.0]: https://github.com/your-repo/novel-cli/compare/v0.3.0...v0.4.0

[v0.3.0]: https://github.com/your-repo/novel-cli/compare/v0.2.0...v0.3.0

[v0.2.0]: https://github.com/your-repo/novel-cli/compare/v0.1.0...v0.2.0

[v0.1.0]: https://github.com/your-repo/novel-cli/compare/v0.0.0...v0.1.0

[Unreleased]: https://github.com/your-repo/novel-cli/compare/master...HEAD
