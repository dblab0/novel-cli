"""技能生成评估任务。

该模块提供技能生成评估任务的完整实现，包括：
- 用例生成：从 CSV 实体数据中抽取高频实体生成评估用例
- 用例执行：调用 Runner 子进程执行评估（自定义 work_dir 清理策略）
- 评分判断：使用 5 维度 Judge LLM 评分

与 ToolUsageTask 的核心差异：
- work_dir 清理策略不同：保留 5 个设定目录（characters/items/locations/organizations/skills）
- Judge 使用 5 维度评分（含 setting_completeness 和 format_compliance）
- Case 生成无需 LLM，直接从 CSV 抽取
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from tqdm import tqdm

from novel_eval.models import CaseResult, EvalCase, EvalScore
from novel_eval.runner import EvalRunner, _SETTING_DIRS
from novel_eval.tasks.base import BaseTask
from novel_eval.tasks.registry import register_task
from novel_eval.tasks.skill_generation.generator import (
    generate_cases as _generate_cases,
    load_cases_yaml,
    save_cases_yaml,
    save_cases_yaml_append,
)
from novel_eval.tasks.skill_generation.judge import (
    SkillGenJudge,
    load_skill_template,
)
from novel_eval.tasks.tool_usage.models import EvalConfig, TaskConfig


@register_task("skill_generation")
class SkillGenerationTask(BaseTask):
    """技能生成评估任务。

    继承 BaseTask，实现完整的评估流程：
    1. generate: 从 CSV 抽取高频实体生成测试用例
    2. run: 执行用例，收集工具调用记录
    3. judge: 5 维度评分，注入 SKILL.md 模板

    Attributes:
        eval_config: 评估配置对象。
        runner: EvalRunner 实例。
        judge: SkillGenJudge 实例。
    """

    def __init__(self, config: EvalConfig | None = None) -> None:
        """初始化技能生成评估任务。

        Args:
            config: 评估配置对象，如果为 None 则使用默认配置。
        """
        config_dict = config.model_dump() if config else {}
        super().__init__(name="skill_generation", config=config_dict)

        self.eval_config = config or EvalConfig()

        from novel_eval.runner import EvalRunner

        self.runner = EvalRunner(self.eval_config)

        # 初始化 Judge（仅当 API Key 可用时）
        self.judge = (
            SkillGenJudge(self.eval_config.judge)
            if self.eval_config.judge.api_key
            else None
        )

    def generate_cases(
        self,
        book: str,
        settings_dir: Path | None = None,
        seed: int | None = None,
        samples_per_type: int | None = None,
        min_descriptions: int | None = None,
        **kwargs: Any,
    ) -> list[EvalCase]:
        """从 CSV 实体数据中随机抽取高频实体生成评估用例。

        Args:
            book: 书名。
            settings_dir: 未使用（保持接口兼容）。
            seed: 随机种子。
            samples_per_type: 每种类型抽取数量。
            min_descriptions: 最小描述条目数阈值。
            **kwargs: 扩展参数。

        Returns:
            生成的测试用例列表。
        """
        # 从配置中获取 skill_generation 的参数
        sg_config: TaskConfig | None = self.eval_config.tasks.get("skill_generation")

        data_dir = Path(
            sg_config.data_dir if sg_config else "data/input_data"
        )
        min_desc = min_descriptions or (
            sg_config.min_descriptions if sg_config else 20
        )
        min_desc_by_type = (
            sg_config.min_descriptions_by_type if sg_config else {}
        )
        samples = samples_per_type or (
            sg_config.samples_per_type if sg_config else 10
        )

        return _generate_cases(
            book=book,
            data_dir=data_dir,
            min_descriptions=min_desc,
            min_descriptions_by_type=min_desc_by_type,
            samples_per_type=samples,
            seed=seed,
        )

    def save_cases(
        self,
        base_dir: Path,
        cases: list[EvalCase],
        book: str,
        append: bool = False,
    ) -> list[Path]:
        """保存测试用例到 YAML 文件。

        Args:
            base_dir: eval_cases 目录路径。
            cases: 测试用例列表。
            book: 书名。
            append: 是否追加模式。

        Returns:
            保存的 YAML 文件路径列表。
        """
        if append:
            return save_cases_yaml_append(base_dir, cases, book)
        return save_cases_yaml(base_dir, cases, book)

    def load_cases(self, yaml_path: Path) -> list[EvalCase]:
        """从 YAML 文件加载测试用例。

        Args:
            yaml_path: YAML 文件路径。

        Returns:
            EvalCase 列表。
        """
        return load_cases_yaml(yaml_path)

    def _prepare_work_dir(self) -> None:
        """准备工作目录：保留 5 个设定目录，清空其中文件。

        保留 characters/items/locations/organizations/skills 目录，
        只删除其中的文件。其他目录和文件全部删除。
        """
        work_dir = self.runner.work_dir

        if not work_dir.exists():
            work_dir.mkdir(parents=True, exist_ok=True)
            # 确保设定目录存在
            for d in _SETTING_DIRS:
                (work_dir / d).mkdir(exist_ok=True)
            return

        # 遍历 work_dir 内容
        for item in work_dir.iterdir():
            if item.is_dir() and item.name in _SETTING_DIRS:
                # 保留设定目录，只清空其中的文件
                for f in item.iterdir():
                    if f.is_file():
                        f.unlink()
                    elif f.is_dir():
                        shutil.rmtree(f)
            elif item.is_dir():
                # 非设定目录，整个删除
                shutil.rmtree(item)
            else:
                # 非目录文件，直接删除
                item.unlink()

        # 确保设定目录存在
        for d in _SETTING_DIRS:
            (work_dir / d).mkdir(exist_ok=True)

    async def run_case(self, case: EvalCase, runner: EvalRunner | None = None, **kwargs: Any) -> CaseResult:
        """执行单个测试用例。

        使用自定义的 work_dir 清理策略（保留设定目录），不调用
        EvalRunner._prepare_session() 以避免其清空所有子目录。

        Args:
            case: 待执行的测试用例。
            runner: 未使用（保持接口兼容）。
            **kwargs: 扩展参数。

        Returns:
            用例执行结果。
        """
        # 1. 自定义 work_dir 清理（保留设定目录）
        self._prepare_work_dir()

        # 2. 清除旧 session 目录
        session_id = f"eval-{case.id}"
        session_dir = self.runner.sessions_dir / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)

        # 3. 构建命令并执行子进程（绕过 _prepare_session）
        cmd = self.runner._build_command(case)
        start_time = time.time()

        try:
            subprocess.run(cmd, timeout=self.runner.timeout, check=True, capture_output=True)
        except subprocess.TimeoutExpired:
            execution_time = time.time() - start_time
            return CaseResult(
                case_id=case.id,
                question=case.question,
                tool_calls=[],
                final_answer="",
                execution_time=execution_time,
                error=f"执行超时（{self.runner.timeout}秒）",
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

        # 4. 解析 wire.jsonl
        execution_time = time.time() - start_time
        wire_path = self.runner.sessions_dir / session_id / "wire.jsonl"
        return self.runner._parse_wire(wire_path, case, execution_time)

    async def run_batch(self, cases: list[EvalCase]) -> list[CaseResult]:
        """批量执行测试用例。

        逐个执行用例（每个用例前清理 work_dir），使用进度条显示。

        Args:
            cases: 测试用例列表。

        Returns:
            执行结果列表。
        """
        results: list[CaseResult] = []
        for case in tqdm(cases, desc="执行评估", unit="用例"):
            result = await self.run_case(case)
            results.append(result)
        return results

    def _judge_case_sync(
        self,
        case_result: CaseResult,
        case: EvalCase,
    ) -> EvalScore:
        """同步执行评分。

        Args:
            case_result: 用例执行结果。
            case: 原始测试用例（用于获取 meta 信息）。

        Returns:
            EvalScore 评分结果。
        """
        # 获取 skill_type 并加载 SKILL.md 模板
        skill_type = case.meta.get("skill_type", "")
        skill_template = load_skill_template(skill_type)
        dim_keys = [d["key"] for d in self.dimensions] if self.dimensions else None

        # 使用 Judge 评分
        meta = case.meta if case else None
        if self.judge:
            return self.judge.evaluate(
                case_result,
                skill_template=skill_template,
                dimensions=dim_keys,
                meta=meta,
            )

        # 无 Judge 实例时使用函数式调用
        from novel_eval.tasks.skill_generation.judge import judge_case

        return judge_case(
            case_result,
            self.eval_config.judge.model_dump(),
            skill_template=skill_template,
            dimensions=dim_keys,
            meta=meta,
        )

    async def judge_case(
        self,
        case_result: CaseResult,
        judge_llm: Any = None,
        **kwargs: Any,
    ) -> EvalScore:
        """评估单个用例结果。

        通过 asyncio.to_thread 避免同步 httpx 阻塞事件循环。

        Args:
            case_result: 用例执行结果。
            judge_llm: Judge LLM 实例（未使用）。
            **kwargs: 需包含 case 参数（原始 EvalCase）。

        Returns:
            EvalScore 评分结果。
        """
        case = kwargs.get("case")
        if case is None:
            raise ValueError("judge_case 需要传入 case 参数")
        return await asyncio.to_thread(self._judge_case_sync, case_result, case)

    async def evaluate(
        self,
        cases: list[EvalCase],
        output_dir: Path | None = None,
        skip_run: bool = False,
        existing_results: list[CaseResult] | None = None,
    ) -> list[tuple[CaseResult, EvalScore]]:
        """完整评估流程。

        串联 run -> judge 流程。

        Args:
            cases: 测试用例列表。
            output_dir: 结果输出目录。
            skip_run: 是否跳过执行阶段。
            existing_results: 已有的 CaseResult 列表。

        Returns:
            (CaseResult, EvalScore) 元组列表。
        """
        # 1. 执行阶段
        if skip_run:
            if existing_results is None:
                raise ValueError("skip_run 模式需要提供 existing_results")
            results = existing_results
        else:
            results = await self.run_batch(cases)

        # 2. 构建用例映射
        case_map = {c.id: c for c in cases}

        # 3. 评分阶段
        semaphore = asyncio.Semaphore(self.eval_config.concurrency)

        async def judge_one(
            idx: int, result: CaseResult
        ) -> tuple[int, tuple[CaseResult, EvalScore]]:
            """评分单个用例。"""
            async with semaphore:
                case = case_map.get(result.case_id)
                if case is None:
                    raise ValueError(f"找不到用例 {result.case_id}")
                score = await asyncio.to_thread(
                    self._judge_case_sync, result, case
                )
                return (idx, (result, score))

        tasks = [asyncio.create_task(judge_one(i, r)) for i, r in enumerate(results)]
        pairs: list[tuple[CaseResult, EvalScore]] = [None] * len(tasks)  # type: ignore[list-item]

        try:
            with tqdm(total=len(tasks), desc="Judge 评分", unit="用例") as pbar:
                for coro in asyncio.as_completed(tasks):
                    idx, pair = await coro
                    pairs[idx] = pair
                    pbar.update(1)
        except BaseException:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        return pairs
