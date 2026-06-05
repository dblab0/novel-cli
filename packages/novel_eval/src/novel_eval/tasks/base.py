"""
评估任务抽象基类。

定义评估任务的通用接口，所有具体任务需实现三个核心方法：
- generate_cases: 生成测试用例
- run_case: 执行单个测试用例
- judge_case: 评估单个用例结果

通过抽象基类实现任务的可插拔性，后续新增评估任务只需在 tasks/ 下
新增目录并实现 BaseTask 即可。
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from novel_eval.models import CaseResult, EvalCase, EvalScore
from novel_eval.runner import EvalRunner


class BaseTask(ABC):
    """评估任务抽象基类。

    定义评估任务的通用接口，所有具体任务需实现三个核心方法。

    Attributes:
        name: 任务名称标识。
        config: 任务配置字典。
    """

    def __init__(self, name: str, config: dict | None = None) -> None:
        """初始化任务。

        Args:
            name: 任务名称标识，如 "tool_usage"。
            config: 任务配置字典。
        """
        self.name = name
        self.config = config or {}
        self.dimensions: list[dict] = self._load_dimensions()

    def _load_dimensions(self) -> list[dict]:
        """从 config.tasks[name].dimensions 加载维度定义。

        Returns:
            维度配置列表。无配置时返回空列表（向后兼容）。
        """
        tasks_cfg = self.config.get("tasks", {})
        task_cfg = tasks_cfg.get(self.name, {})
        return task_cfg.get("dimensions", [])

    def load_existing_results(self, cases: list[EvalCase], result_dir: Path) -> list[CaseResult]:
        """skip-run 模式：加载已有执行结果。

        默认实现：从 result_dir/cases/*.json 加载 CaseResult。
        子类可覆盖此方法提供不同的加载策略。

        Args:
            cases: 测试用例列表（用于匹配）。
            result_dir: 结果目录路径。

        Returns:
            CaseResult 列表。
        """
        from novel_eval.models import load_case_results_from_dir
        return load_case_results_from_dir(result_dir)

    def detect_missing(self, cases: list[EvalCase]) -> list[EvalCase]:
        """rerun-missing 模式：检测需要重跑的用例。

        默认实现：返回空列表（表示不支持 rerun-missing）。

        Args:
            cases: 全量测试用例列表。

        Returns:
            需要重跑的用例列表。
        """
        return []

    @property
    def supports_rerun_missing(self) -> bool:
        """是否支持 rerun-missing 模式。默认 False。"""
        return False

    @abstractmethod
    def generate_cases(
        self,
        book: str,
        settings_dir: Path,
        **kwargs: Any,
    ) -> list[EvalCase]:
        """生成测试用例。

        根据设定文件生成测试用例，包含问题及其元数据。
        生成后可进行脚本预跑验证，填充 _validation 字段。

        Args:
            book: 书籍名称，如 "凡人修仙传"。
            settings_dir: 设定文件目录路径。
            **kwargs: 子类扩展参数（如 level、eval_model、instruction 等）。

        Returns:
            生成的测试用例列表。
        """
        pass

    @abstractmethod
    async def run_case(
        self,
        case: EvalCase,
        runner: EvalRunner | None = None,
        **kwargs: Any,
    ) -> CaseResult:
        """执行单个测试用例。

        启动 CLI 子进程执行测试用例，收集工具调用记录和最终回答。

        Args:
            case: 待执行的测试用例。
            runner: 评估运行器实例，子类可设为可选。
            **kwargs: 子类扩展参数。

        Returns:
            用例执行结果，包含工具调用记录和最终回答。
        """
        pass

    @abstractmethod
    async def judge_case(
        self,
        case_result: CaseResult,
        judge_llm: Any = None,
        **kwargs: Any,
    ) -> EvalScore:
        """评估单个用例结果。

        使用 Judge LLM 对用例执行结果进行评分。

        Args:
            case_result: 用例执行结果。
            judge_llm: Judge LLM 实例，子类可设为可选。
            **kwargs: 子类扩展参数。

        Returns:
            评估评分，包含各维度分数和评语。
        """
        pass
