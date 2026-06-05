# 更新日志

<!--
Release notes will be parsed and available as /release-notes
The parser extracts for each version:
  - a short description (first paragraph after the version header)
  - bullet entries beginning with "- " under that version (across any subsections)
Internal builds may append content to the Unreleased section.
Only write entries that are worth mentioning to users.
-->

## Unreleased

## 0.2.0 (2026-04-15)

基于 Kimi CLI 的魔改版本，重新定位为 Novel CLI。

- 移除 OAuth 登录功能，改用 config.toml 中配置 API Key 认证
- 新增 `novel setup` 命令，提供交互式配置 provider/model 入口
- 首次运行 Shell 模式时自动引导用户完成配置
- 添加 OpenRouter provider 支持
- 添加智谱 AI provider 支持

## 0.1.0 (2026-04-15)

从 Kimi CLI 项目移植初始化版本，完成基础可用状态。

- 从 Kimi CLI Fork 并完成项目初始化
- 重命名为 Novel CLI，调整品牌标识
- 保留核心 Agent Loop、工具系统、会话管理等基础能力
- 支持 OpenAI 兼容协议的 LLM provider
- 支持 MCP (Model Context Protocol) 工具集成
