"""
CLI 子进程运行器模块。

通过 CLI 子进程驱动 novel-cli，从 wire.jsonl 提取工具调用记录。

核心功能：
1. 构建并执行 novel-cli 子进程命令
2. 管理 session 隔离（每次运行前清除旧 session）
3. 解析 wire.jsonl 文件提取 ToolCall、ToolResult、TextPart 消息

运行流程：
    1. 准备工作目录和 session 目录
    2. 清除旧 session 目录防止上下文残留
    3. 启动子进程执行 novel-cli（静默运行）
    4. 解析 wire.jsonl 提取工具调用记录
    5. 返回 CaseResult

命令示例：
    novel-cli --print --work-dir ~/novel_test --session eval-L1-001 \\
        --agent-file agents/novel/agent.yaml --book "凡人修仙传" --model deepseek-v3 -p "韩立是谁？"

session 路径：
    ~/.novel/sessions/<md5(work_dir)>/<session_id>/wire.jsonl
"""

import asyncio
import json
import shutil
import subprocess
import time
from hashlib import md5
from pathlib import Path
from typing import TYPE_CHECKING

from tqdm.asyncio import tqdm_asyncio

from novel_cli.share import get_share_dir

from novel_eval.models import CaseResult, EvalCase, ToolCallRecord

# 保留的设定目录列表，清理 work_dir 时只清空其中的文件而不删除目录本身
_SETTING_DIRS = frozenset(["characters", "items", "locations", "organizations", "skills"])

if TYPE_CHECKING:
    from novel_eval.tasks.tool_usage.models import EvalConfig


def resolve_sessions_dir(work_dir: Path) -> Path:
    """解析 sessions 目录路径。

    Args:
        work_dir: 工作目录路径。

    Returns:
        sessions 目录路径，格式为 ~/.novel/sessions/<md5(work_dir)>/
    """
    work_dir_str = str(work_dir.expanduser().resolve())
    dir_hash = md5(work_dir_str.encode()).hexdigest()
    return get_share_dir() / "sessions" / dir_hash


class EvalRunner:
    """评估运行器。

    通过 CLI 子进程驱动 novel-cli，从 wire.jsonl 收集结果。

    Attributes:
        config: 评估配置。
        work_dir: 工作目录。
        sessions_dir: sessions 目录路径。
        timeout: 子进程超时时间（秒）。
    """

    def __init__(self, config: "EvalConfig") -> None:
        """初始化运行器。

        Args:
            config: 评估配置对象。
        """
        self.config = config
        self.work_dir = Path(config.runner.work_dir).expanduser()
        self.sessions_dir = resolve_sessions_dir(self.work_dir)
        self.timeout = config.runner.timeout

    def _build_command(self, case: EvalCase) -> list[str]:
        """构建 novel-cli 子进程命令行参数。

        Args:
            case: 评估用例。

        Returns:
            命令行参数列表。
        """
        session_id = f"eval-{case.id}"
        agent_file = self.config.runner.agent_file
        cmd = [
            "novel-cli", "--print",
            "--work-dir", str(self.work_dir),
            "--session", session_id,
            "--agent-file", agent_file,
            "--book", case.book,
            "-p", case.question,
        ]
        # model 从 eval_config.yaml 的 runner.eval_model 读取
        if self.config.runner.eval_model:
            cmd.extend(["--model", self.config.runner.eval_model])
        return cmd

    def _prepare_session(self, case: EvalCase) -> str:
        """准备工作目录和 session 目录。

        每次执行前清空 work_dir 内容（保留设定目录 characters/items/locations/
        organizations/skills，只清空其中的文件），并清除旧 session 目录以防止上下文残留。

        Args:
            case: 评估用例。

        Returns:
            session_id 字符串。
        """
        # 清空 work_dir 内容，防止上次执行残留污染
        # 保留设定目录（characters/items/locations/organizations/skills），只清空其中的文件
        if self.work_dir.exists():
            for item in self.work_dir.iterdir():
                try:
                    if item.is_dir() and item.name in _SETTING_DIRS:
                        # 保留设定目录，只删除其中的文件
                        for f in item.iterdir():
                            if f.is_file():
                                f.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except FileNotFoundError:
                    pass
        self.work_dir.mkdir(parents=True, exist_ok=True)

        session_id = f"eval-{case.id}"
        session_dir = self.sessions_dir / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)
        return session_id

    def _parse_wire(self, wire_path: Path, case: EvalCase, execution_time: float) -> CaseResult:
        """解析 wire.jsonl 文件，提取工具调用记录和最终回答。

        wire.jsonl 格式示例：
        {"timestamp": 1714567890.123, "message": {
            "type": "ToolCall",
            "payload": {"id": "call_123", "function": {"name": "search", "arguments": "..."}}
        }}

        Args:
            wire_path: wire.jsonl 文件路径。
            case: 评估用例。
            execution_time: 执行耗时（秒）。

        Returns:
            用例执行结果，包含工具调用记录和最终回答。
        """
        if not wire_path.exists():
            return CaseResult(
                case_id=case.id,
                question=case.question,
                tool_calls=[],
                final_answer="",
                execution_time=execution_time,
                error="wire.jsonl 文件不存在",
            )

        tool_calls: list[ToolCallRecord] = []
        final_answer = ""
        step = 0

        # 用于匹配 ToolResult 到对应的 ToolCall
        pending_results: dict[str, int] = {}  # call_id -> step index

        # ToolCallPart 流式累积状态：LLM 流式输出时，arguments 被拆成
        # ToolCall（首帧）+ 多个 ToolCallPart（后续帧），需累积后才能解析
        pending_args_str = ""   # 当前未完成的 arguments 字符串
        pending_call_idx = -1   # 对应 ToolCallRecord 在 tool_calls 中的索引

        def _flush_pending_args() -> None:
            """将累积的 arguments 字符串解析为 params 并写入 ToolCallRecord。"""
            nonlocal pending_args_str, pending_call_idx
            if pending_call_idx >= 0:
                try:
                    args = json.loads(pending_args_str) if pending_args_str else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls[pending_call_idx].params = args
            pending_args_str = ""
            pending_call_idx = -1

        try:
            with open(wire_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    msg = entry.get("message", {})
                    msg_type = msg.get("type")

                    if msg_type == "ToolCall":
                        _flush_pending_args()

                        payload = msg.get("payload", {})
                        call_id = payload.get("id", "")
                        func = payload.get("function", {})
                        args_str = func.get("arguments", "")

                        # 直接使用原始工具名，不做映射
                        tool_name = func.get("name", "")
                        step += 1

                        tool_calls.append(ToolCallRecord(
                            step=step,
                            tool_name=tool_name,
                            params={},
                            result_summary="",
                        ))

                        # 记录 pending 状态，等待后续 ToolCallPart 到齐后统一解析
                        pending_args_str = args_str
                        pending_call_idx = len(tool_calls) - 1

                        if call_id:
                            pending_results[call_id] = len(tool_calls) - 1

                    elif msg_type == "ToolCallPart":
                        # 累积 arguments 碎片到当前 ToolCall
                        payload = msg.get("payload", {})
                        part = payload.get("arguments_part", "")
                        if part and pending_call_idx >= 0:
                            pending_args_str += part

                    elif msg_type == "ToolResult":
                        # ToolResult 不触发 flush：并行调用时 ToolCallPart 可能在 ToolResult 之后
                        payload = msg.get("payload", {})
                        call_id = payload.get("tool_call_id", "")
                        return_value = payload.get("return_value", {})
                        content = return_value.get("output", "")

                        if call_id in pending_results:
                            idx = pending_results[call_id]
                            tool_calls[idx].result_summary = content

                    elif msg_type == "TextPart":
                        payload = msg.get("payload", {})
                        text = payload.get("text", "")
                        if text:
                            final_answer = text

                    elif msg_type == "ContentPart":
                        payload = msg.get("payload", {})
                        if payload.get("type") == "text":
                            text = payload.get("text", "")
                            if text:
                                final_answer = text

            # 循环结束后刷新最后一个 pending
            _flush_pending_args()

        except Exception as e:
            return CaseResult(
                case_id=case.id,
                question=case.question,
                tool_calls=tool_calls,
                final_answer=final_answer,
                execution_time=execution_time,
                error=f"解析 wire.jsonl 失败: {e}",
            )

        return CaseResult(
            case_id=case.id,
            question=case.question,
            tool_calls=tool_calls,
            final_answer=final_answer,
            execution_time=execution_time,
        )

    def detect_missing_cases(self, cases: list[EvalCase]) -> list[EvalCase]:
        """检测 sessions 目录中缺失 wire.jsonl 的用例。

        遍历所有用例，检查对应的 session 目录中是否存在 wire.jsonl 文件，
        返回不存在的用例列表。

        Args:
            cases: 全量用例列表。

        Returns:
            缺失 wire.jsonl 的用例子列表。
        """
        missing = []
        for case in cases:
            session_id = f"eval-{case.id}"
            wire_path = self.sessions_dir / session_id / "wire.jsonl"
            if not wire_path.exists():
                missing.append(case)
        return missing

    def re_extract_results(self, cases: list[EvalCase]) -> list[CaseResult]:
        """从 wire.jsonl 重新提取 CaseResult，补充 result_summary 等字段。

        用于 --skip-run 模式：跳过 agent 执行，但用修正后的解析逻辑
        从已有的 wire.jsonl 重新提取完整数据（如之前 result_summary 为空）。

        Args:
            cases: 用例列表（从中提取 case_id 和 question）。

        Returns:
            重新提取的用例执行结果列表。wire.jsonl 不存在时返回空 result。
        """
        results: list[CaseResult] = []
        for case in cases:
            session_id = f"eval-{case.id}"
            wire_path = self.sessions_dir / session_id / "wire.jsonl"
            if wire_path.exists():
                results.append(self._parse_wire(wire_path, case, 0.0))
            else:
                results.append(CaseResult(
                    case_id=case.id,
                    question=case.question,
                    tool_calls=[],
                    final_answer="",
                    execution_time=0.0,
                    error="wire.jsonl 不存在",
                ))
        return results

    def run_case(self, case: EvalCase) -> CaseResult:
        """同步执行单个问题，解析 wire.jsonl 返回结果。

        执行流程：
        1. 准备工作目录和 session 目录
        2. 构建命令行参数
        3. 启动子进程执行 novel-cli
        4. 解析 wire.jsonl 提取结果

        Args:
            case: 评估用例。

        Returns:
            用例执行结果，包含工具调用记录和最终回答。
        """
        session_id = self._prepare_session(case)
        cmd = self._build_command(case)
        start_time = time.time()

        try:
            subprocess.run(cmd, timeout=self.timeout, check=True, capture_output=True)
        except subprocess.TimeoutExpired:
            execution_time = time.time() - start_time
            return CaseResult(
                case_id=case.id,
                question=case.question,
                tool_calls=[],
                final_answer="",
                execution_time=execution_time,
                error=f"执行超时（{self.timeout}秒）",
            )
        except subprocess.CalledProcessError as e:
            execution_time = time.time() - start_time
            return CaseResult(
                case_id=case.id,
                question=case.question,
                tool_calls=[],
                final_answer="",
                execution_time=execution_time,
                error=f"子进程执行失败: 返回码 {e.returncode}",
            )
        except Exception as e:
            execution_time = time.time() - start_time
            return CaseResult(
                case_id=case.id,
                question=case.question,
                tool_calls=[],
                final_answer="",
                execution_time=execution_time,
                error=f"执行异常: {e}",
            )

        execution_time = time.time() - start_time
        wire_path = self.sessions_dir / session_id / "wire.jsonl"
        return self._parse_wire(wire_path, case, execution_time)

    async def run_batch(self, cases: list[EvalCase]) -> list[CaseResult]:
        """批量执行用例，支持并发控制和进度显示。

        使用 asyncio.Semaphore 控制并发数，通过 asyncio.to_thread 在线程池中
        运行同步的 run_case 方法。使用 tqdm 显示进度条。

        Args:
            cases: 评估用例列表。

        Returns:
            用例执行结果列表，顺序与输入 cases 一致。
        """
        concurrency = self.config.concurrency
        semaphore = asyncio.Semaphore(concurrency)

        async def run_with_semaphore(case: EvalCase) -> CaseResult:
            """使用信号量控制并发执行单个用例。"""
            async with semaphore:
                # 在线程池中运行同步的 run_case
                return await asyncio.to_thread(self.run_case, case)

        # 使用 tqdm_asyncio.gather 显示进度条
        results = await tqdm_asyncio.gather(
            *[run_with_semaphore(c) for c in cases],
            desc="执行评估",
            unit="用例",
        )
        return list(results)