"""工具使用评估任务。

该模块提供工具使用评估任务的完整实现，包括：
- 用例生成：从设定文件生成测试问题
- 用例加载：从 YAML 文件加载测试用例
- 用例执行：调用 Runner 子进程执行评估
- 评分判断：使用 Judge LLM 对结果进行评分

使用方式：
    from novel_eval.tasks.tool_usage import ToolUsageTask
    from novel_eval.tasks.tool_usage.models import EvalConfig

    cfg = EvalConfig()
    task = ToolUsageTask(cfg)

    # 生成用例
    cases = task.generate_cases("凡人修仙传", Path("/path/to/settings"))

    # 加载用例
    cases = task.load_cases(Path("eval_cases/凡人修仙传/level_1_entity.yaml"))

    # 执行评估
    results = await task.evaluate(cases)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tqdm import tqdm

from novel_eval.models import CaseResult, EvalCase, EvalScore, load_case_results_from_dir
from novel_eval.tasks.base import BaseTask
from novel_eval.tasks.registry import register_task
from novel_eval.tasks.tool_usage.generator import (
    generate_cases,
    get_scenario_filename,
    load_cases_yaml,
    save_cases_yaml,
)
from novel_eval.tasks.tool_usage.judge import Judge, judge_case
from novel_eval.tasks.tool_usage.models import EvalConfig

if TYPE_CHECKING:
    from novel_eval.runner import EvalRunner


@register_task("tool_usage")
class ToolUsageTask(BaseTask):
    """工具使用评估任务。

    继承 BaseTask，实现完整的评估流程：
    1. generate: 从 YAML 加载或生成测试用例
    2. run: 执行用例，收集工具调用记录
    3. judge: 评分，生成评估结果

    Attributes:
        eval_config: 评估配置对象。
        runner: EvalRunner 实例，用于执行测试用例。
        judge: Judge 实例，用于评分。
    """

    def __init__(self, config: EvalConfig | None = None) -> None:
        """初始化工具使用评估任务。

        Args:
            config: 评估配置对象，如果为 None 则使用默认配置。
        """
        # 转换为 dict 传递给 BaseTask
        config_dict = config.model_dump() if config else {}
        super().__init__(name="tool_usage", config=config_dict)

        # 保存完整的配置对象
        self.eval_config = config or EvalConfig()

        # 延迟导入 Runner 避免循环导入
        from novel_eval.runner import EvalRunner

        # 初始化 Runner
        self.runner: EvalRunner = EvalRunner(self.eval_config)

        # 初始化 Judge（仅当 API Key 可用时）
        self.judge = Judge(self.eval_config.judge) if self.eval_config.judge.api_key else None

    def generate_cases(
        self,
        book: str,
        settings_dir: Path,
        scenario: str = "level_1_entity",
        instruction: str | None = None,
        id_offset: int = 0,
    ) -> list[EvalCase]:
        """生成测试用例。

        通过 LLM 读取设定文件，按场景生成测试问题。

        Args:
            book: 书名，如 "凡人修仙传"。
            settings_dir: 设定文件目录路径。
            scenario: 场景名称，如 "level_1_entity"。
            instruction: 用户自定义指令，用于引导问题生成方向。
            id_offset: ID 序号偏移量，用于追加模式下避免 ID 冲突。默认 0。

        Returns:
            生成的测试用例列表。
        """
        # 从配置获取 LLM 信息（用于生成问题）
        llm_config = self.eval_config.agent.model_dump()
        return generate_cases(
            book,
            settings_dir,
            scenario=scenario,
            llm_config=llm_config,
            instruction=instruction,
            id_offset=id_offset,
        )

    def save_cases(
        self,
        base_dir: Path,
        cases: list[EvalCase],
        book: str,
        scenario: str,
        existing_cases: list[EvalCase] | None = None,
    ) -> None:
        """保存测试用例到 YAML 文件。

        Args:
            base_dir: eval_cases 目录路径。
            cases: 新生成的用例列表。
            book: 书名。
            scenario: 场景名称，如 "level_1_entity"。
            existing_cases: 已有用例列表。不为 None 时合并后保存（追加模式）。
        """
        save_cases_yaml(base_dir, cases, book, scenario, existing_cases=existing_cases)

    def load_cases(self, yaml_path: Path) -> list[EvalCase]:
        """从 YAML 文件加载测试用例。

        Args:
            yaml_path: YAML 文件路径。

        Returns:
            EvalCase 列表。
        """
        return load_cases_yaml(yaml_path)

    async def run_case(self, case: EvalCase, runner: EvalRunner | None = None) -> CaseResult:
        """执行单个测试用例。

        启动 CLI 子进程执行测试用例，收集工具调用记录和最终回答。

        Args:
            case: 待执行的测试用例。
            runner: 评估运行器实例，如果为 None 则使用内置 runner。

        Returns:
            用例执行结果，包含工具调用记录和最终回答。
        """
        use_runner = runner or self.runner
        # run_case 是同步方法，直接调用
        return use_runner.run_case(case)

    async def run_batch(self, cases: list[EvalCase]) -> list[CaseResult]:
        """批量执行测试用例。

        使用配置的并发数执行多个测试用例。

        Args:
            cases: 测试用例列表。

        Returns:
            执行结果列表，顺序与输入一致。
        """
        return await self.runner.run_batch(cases)

    def _judge_case_sync(self, case_result: CaseResult, meta: dict | None = None) -> EvalScore:
        """同步执行评分（内部调用同步 httpx），供 asyncio.to_thread 使用。

        Args:
            case_result: 用例执行结果。
            meta: 用例元数据，包含 expected_tool_types / involved_entities 等信息。

        Returns:
            评估评分，包含各维度分数和评语。
        """
        dim_keys = [d["key"] for d in self.dimensions] if self.dimensions else None
        if self.judge:
            return self.judge.evaluate(case_result, dimensions=dim_keys, meta=meta)

        judge_config = {
            "model": self.eval_config.judge.model,
            "base_url": self.eval_config.judge.base_url,
            "api_key": self.eval_config.judge.api_key,
            "api_key_env": self.eval_config.judge.api_key_env,
        }
        return judge_case(case_result, judge_config, dimensions=dim_keys, meta=meta)

    async def judge_case(self, case_result: CaseResult, judge_llm: Any = None) -> EvalScore:
        """评估单个用例结果。

        使用 Judge LLM 对用例执行结果进行评分。
        通过 asyncio.to_thread 避免同步 httpx 阻塞事件循环。

        Args:
            case_result: 用例执行结果。
            judge_llm: Judge LLM 实例（当前未使用，预留扩展）。

        Returns:
            评估评分，包含各维度分数和评语。
        """
        return await asyncio.to_thread(self._judge_case_sync, case_result)

    async def evaluate(
        self,
        cases: list[EvalCase],
        output_dir: Path | None = None,
        skip_run: bool = False,
        existing_results: list[CaseResult] | None = None,
    ) -> list[tuple[CaseResult, EvalScore]]:
        """完整评估流程。

        串联 run → judge 流程，完成完整评估周期。
        支持 skip_run 模式，仅进行 Judge 评分。

        Args:
            cases: 测试用例列表（skip_run 模式下可为空）。
            output_dir: 结果输出目录（预留，当前未实现）。
            skip_run: 是否跳过 agent 执行阶段，仅进行 Judge 评分。
            existing_results: 已有的 CaseResult 列表（skip_run 模式必填）。

        Returns:
            (CaseResult, EvalScore) 元组列表，包含执行结果和评分。
        """
        # 1. 执行阶段（可选跳过）
        if skip_run:
            if existing_results is None:
                raise ValueError("skip_run 模式需要提供 existing_results")
            results = existing_results
        else:
            results = await self.run_batch(cases)

        # 2. 评分阶段，带进度条和并发控制
        # 构建用例映射，传递 meta 信息到 Judge
        case_map = {c.id: c for c in cases} if cases else {}

        semaphore = asyncio.Semaphore(self.eval_config.concurrency)

        async def judge_one(idx: int, result: CaseResult) -> tuple[int, tuple[CaseResult, EvalScore]]:
            """评估单个用例，返回原始索引和结果元组。"""
            async with semaphore:
                case = case_map.get(result.case_id)
                meta = case.meta if case else None
                # judge_case 内部调用同步 httpx，必须放入线程池避免阻塞事件循环
                score = await asyncio.to_thread(self._judge_case_sync, result, meta)
                return (idx, (result, score))

        # 逐个完成逐个更新进度条，保持原始顺序
        tasks = [asyncio.create_task(judge_one(i, r)) for i, r in enumerate(results)]
        pairs: list[tuple[CaseResult, EvalScore]] = [None] * len(tasks)  # type: ignore[list-item]
        try:
            with tqdm(total=len(tasks), desc="Judge 评分", unit="用例") as pbar:
                for coro in asyncio.as_completed(tasks):
                    idx, pair = await coro
                    pairs[idx] = pair
                    pbar.update(1)
        except BaseException:
            # 异常时取消所有未完成的 task，避免泄漏
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        # 3. 如果指定了输出目录，保存结果（预留扩展点）
        if output_dir:
            # TODO: 实现结果保存逻辑
            pass

        return pairs

    async def evaluate_single(self, case: EvalCase) -> tuple[CaseResult, EvalScore]:
        """执行单个用例的评估。

        Args:
            case: 单个测试用例。

        Returns:
            (CaseResult, EvalScore) 元组。
        """
        # 执行用例
        result = await self.run_case(case)

        # 评分
        score = await self.judge_case(result)

        return result, score

    def load_existing_results(self, cases: list[EvalCase], result_dir: Path) -> list[CaseResult]:
        """覆盖：从 wire.jsonl 重新解析（而非从 case JSON 加载）。

        Args:
            cases: 测试用例列表。
            result_dir: 结果目录路径。

        Returns:
            CaseResult 列表。
        """
        return self.runner.re_extract_results(cases)

    def detect_missing(self, cases: list[EvalCase]) -> list[EvalCase]:
        """覆盖：基于 sessions 目录中 wire.jsonl 是否存在判断。

        Args:
            cases: 全量测试用例列表。

        Returns:
            需要重跑的用例列表。
        """
        return self.runner.detect_missing_cases(cases)

    @property
    def supports_rerun_missing(self) -> bool:
        """支持 rerun-missing 模式。"""
        return True