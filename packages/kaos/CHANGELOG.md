# 更新日志

## 未发布

## 0.9.0 (2026-04-02)

- 测试：添加 `test_glob_includes_hidden_files` 以验证 glob 匹配点文件和隐藏目录

## 0.8.0 (2026-04-01)

- 修复 `writetext` 在 Windows 上将 LF 转换为 CRLF 的问题 — 使用 `newline=""` 打开文件以禁用 Python 的通用换行符转换写入

## 0.7.0 (2026-02-06)

- 为 `exec()` 方法添加 `env` 参数，用于向子进程传递环境变量

## 0.6.0 (2026-01-09)

- 为 `readbytes` 添加可选的 `n` 参数，仅读取前 n 个字节

## 0.5.4 (2026-01-06)

- 放宽 `aiofiles` 依赖版本至 `>=24.0,<26.0`

## 0.5.3 (2025-12-29)

- 为 `SSHKaos` 添加 `host` 属性

## 0.5.2 (2025-12-17)

- 修复 `SSHKaos.Process.wait` 不清空 stdout/stderr 缓冲区的问题
- 如果 `SSHKaos.Process.wait` 未获取到返回码，则返回 1 作为返回码

## 0.5.1 (2025-12-15)

- 修复文件不存在时 `SSHKaos.stat` 抛出未处理异常的问题
- 修复无 CWD 时 `SSHKaos.exec` 的问题
- 修复 `SSHKaos.iterdir` 返回 `KaosPath`

## 0.5.0 (2025-12-12)

- 将 `KaosProcess` 移动到 `Kaos.Process`
- 添加 `AsyncReadable` 和 `AsyncWritable` 协议
- 添加 `SSHKaos` 实现
- 将 Python 版本要求降低至 3.12

## 0.4.0 (2025-12-06)

- 添加 `Kaos.exec` 方法用于执行命令
- 添加 `StepResult` 作为 `Kaos.stat` 的返回类型

## 0.3.0 (2025-12-03)

- 将 `iterdir`、`glob` 和 `read_lines` 改为返回 `AsyncIterator` 的同步函数

## 0.2.0 (2025-12-01)

- 初始版本，包含 `Kaos` 协议、`LocalKaos` 实现和用于便捷文件操作的 `KaosPath`
