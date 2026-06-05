"""评估数据索引模块。

扫描 eval_results 目录结构，构建内存索引，提供查询接口。

目录结构约定：
    eval_results/{book}/{agent}/{scenario}/{model}_{timestamp}/
    ├── report.json          # 结构化评估报告
    └── cases/
        ├── L1-001.json      # 用例评估结果
        ├── L1-001/
        │   └── messages.md  # 上下文消息记录（可选）
        └── ...
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# run 目录名格式：{model}_{timestamp}，timestamp 为 YYYY-MM-DD_HHMMSS
_RUN_DIR_PATTERN = re.compile(r"^(.+)_(\d{4}-\d{2}-\d{2}_\d{6})$")


@dataclass
class RunInfo:
    """单次评估运行信息。

    Attributes:
        book: 书籍名称。
        agent: Agent 名称。
        scenario: 场景名称。
        run_id: 运行标识（目录名，如 model_2026-04-26_123456）。
        model: 模型标识。
        timestamp: 时间戳字符串（YYYY-MM-DD_HHMMSS）。
        dir_path: 运行目录的绝对路径。
        report_data: report.json 的解析数据，加载失败时为 None。
        cases: 该运行下的用例 ID 列表。
        task_type: 任务类型，从 report_data 读取，默认 "tool_usage"。
    """

    book: str
    agent: str
    scenario: str
    run_id: str
    model: str
    timestamp: str
    dir_path: Path
    report_data: dict[str, Any] | None = None
    cases: list[str] = field(default_factory=list)
    task_type: str = "tool_usage"


class EvalDataIndex:
    """评估数据内存索引。

    扫描 eval_results 目录，构建以 run_id 为 key 的索引，
    支持按 book / agent / scenario 维度查询。

    Attributes:
        eval_dir: 评估结果根目录路径。
        _runs: run_id → RunInfo 的映射。
    """

    def __init__(self, eval_dir: Path) -> None:
        """初始化索引并扫描目录。

        Args:
            eval_dir: 评估结果根目录路径，包含 {book}/{agent}/{scenario}/ 层级。
        """
        self.eval_dir = Path(eval_dir)
        self._runs: dict[str, RunInfo] = {}
        self._scan()

    # ------------------------------------------------------------------
    # 内部扫描逻辑
    # ------------------------------------------------------------------

    def _scan(self) -> None:
        """扫描 eval_dir 目录，构建完整的内存索引。"""
        self._runs.clear()

        if not self.eval_dir.exists():
            logger.warning("评估结果目录不存在: %s", self.eval_dir)
            return

        # 遍历 eval_results/{book}/{agent}/{scenario}/{run_dir}/
        for run_dir in sorted(self.eval_dir.rglob("*")):
            if not run_dir.is_dir():
                continue

            # 检查是否为 run 目录（包含 report.json）
            report_file = run_dir / "report.json"
            if not report_file.exists():
                continue

            # 从路径提取 book, agent, scenario
            # 相对于 eval_dir 的路径部分：{book}/{agent}/{scenario}/{run_id}
            try:
                relative = run_dir.relative_to(self.eval_dir)
                parts = relative.parts
            except ValueError:
                continue

            if len(parts) != 4:
                # 不是标准的 run 目录层级，跳过
                continue

            book, agent, scenario, run_id = parts

            # 解析 run_id 中的 model 和 timestamp
            match = _RUN_DIR_PATTERN.match(run_id)
            if not match:
                logger.debug("跳过不符合命名规范的 run 目录: %s", run_id)
                continue

            model = match.group(1)
            timestamp = match.group(2)

            # 加载 report.json
            report_data = self._load_json(report_file)
            if report_data is None:
                logger.warning("跳过 report.json 加载失败的 run 目录: %s", run_dir)
                continue

            # 扫描 cases 目录
            cases_dir = run_dir / "cases"
            case_ids: list[str] = []
            if cases_dir.exists():
                for case_file in sorted(cases_dir.glob("*.json")):
                    case_id = case_file.stem
                    case_ids.append(case_id)

            # 从 report.json 提取 task_type（默认 tool_usage）
            task_type = "tool_usage"
            if report_data and "task_type" in report_data:
                task_type = report_data["task_type"]

            run_info = RunInfo(
                book=book,
                agent=agent,
                scenario=scenario,
                run_id=run_id,
                model=model,
                timestamp=timestamp,
                dir_path=run_dir,
                report_data=report_data,
                cases=case_ids,
                task_type=task_type,
            )
            self._runs[run_id] = run_info

        logger.info("索引构建完成: %d 个运行", len(self._runs))

    def _load_json(self, path: Path) -> dict[str, Any] | None:
        """加载 JSON 文件，失败时返回 None 并记录警告。

        Args:
            path: JSON 文件路径。

        Returns:
            解析后的字典，失败时返回 None。
        """
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("JSON 文件内容不是字典类型: %s", path)
                return None
            return data
        except json.JSONDecodeError:
            logger.warning("JSON 解析失败: %s", path)
            return None
        except OSError as e:
            logger.warning("读取文件失败: %s (%s)", path, e)
            return None

    # ------------------------------------------------------------------
    # 公共查询方法
    # ------------------------------------------------------------------

    def list_books(self) -> list[str]:
        """返回所有书籍名称。

        Returns:
            去重且排序后的书籍名称列表。
        """
        books: set[str] = set()
        for run in self._runs.values():
            books.add(run.book)
        return sorted(books)

    def list_agents(self, book: str) -> list[str]:
        """返回指定书籍的 agent 列表。

        Args:
            book: 书籍名称。

        Returns:
            去重且排序后的 agent 名称列表，无匹配时返回空列表。
        """
        agents: set[str] = set()
        for run in self._runs.values():
            if run.book == book:
                agents.add(run.agent)
        return sorted(agents)

    def list_scenarios(self, book: str, agent: str) -> list[str]:
        """返回指定 book + agent 下的 scenario 列表。

        Args:
            book: 书籍名称。
            agent: Agent 名称。

        Returns:
            去重且排序后的 scenario 名称列表，无匹配时返回空列表。
        """
        scenarios: set[str] = set()
        for run in self._runs.values():
            if run.book == book and run.agent == agent:
                scenarios.add(run.scenario)
        return sorted(scenarios)

    def list_runs(self, book: str, agent: str, scenario: str) -> list[dict]:
        """返回指定 book + agent + scenario 下的运行批次列表。

        按时间戳升序排列。

        Args:
            book: 书籍名称。
            agent: Agent 名称。
            scenario: 场景名称。

        Returns:
            运行批次列表，每项含 run_id、model、timestamp。
            无匹配时返回空列表。
        """
        matched = []
        for run in self._runs.values():
            if run.book == book and run.agent == agent and run.scenario == scenario:
                matched.append({
                    "run_id": run.run_id,
                    "model": run.model,
                    "timestamp": run.timestamp,
                })
        # 按时间戳升序
        matched.sort(key=lambda x: x["timestamp"])
        return matched

    def list_cases_for_run(self, run_id: str) -> list[dict]:
        """列出指定 run 下的所有用例摘要。

        Args:
            run_id: 运行标识（目录名）。

        Returns:
            用例摘要列表，每项含 case_id 和基础评分信息。
        """
        run = self._runs.get(run_id)
        if run is None:
            return []

        cases_dir = run.dir_path / "cases"
        result: list[dict] = []
        for case_id in run.cases:
            case_file = cases_dir / f"{case_id}.json"
            case_data = self._load_json(case_file)
            if case_data is None:
                # case JSON 格式错误，跳过该 case
                continue

            # 提取摘要信息
            summary: dict[str, Any] = {
                "case_id": case_id,
                "question": case_data.get("question", ""),
                "execution_time": case_data.get("execution_time", 0.0),
                "error": case_data.get("error"),
            }

            # 附加评分信息
            score = case_data.get("score", {})
            if score:
                summary["total_score"] = score.get("total_score", 0.0)
                summary["passed"] = score.get("total_score", 0.0) >= 3.0

            # 附加工具调用统计
            tool_calls = case_data.get("tool_calls", [])
            summary["tool_calls_count"] = len(tool_calls)

            result.append(summary)

        return result

    def get_report(self, run_id: str) -> dict[str, Any] | None:
        """返回指定运行的 report.json 数据。

        对 cases_summary 中缺少 question / execution_time 的条目，
        从独立 case JSON 文件中补充，确保旧数据也能正确显示。

        Args:
            run_id: 运行标识（目录名）。

        Returns:
            report.json 的字典数据。如果 run 不存在则返回 None。
        """
        run = self._runs.get(run_id)
        if run is None:
            return None

        report = run.report_data
        if report is None:
            return None

        # 补充 cases_summary 中缺失的字段
        cases_summary = report.get("cases_summary")
        if cases_summary:
            cases_dir = run.dir_path / "cases"
            for item in cases_summary:
                if "question" in item and "execution_time" in item:
                    continue
                case_id = item.get("case_id", "")
                case_file = cases_dir / f"{case_id}.json"
                case_data = self._load_json(case_file)
                if case_data is not None:
                    if "question" not in item:
                        item["question"] = case_data.get("question", "")
                    if "execution_time" not in item:
                        item["execution_time"] = case_data.get("execution_time")

        return report

    def get_run_info(self, run_id: str) -> dict[str, Any] | None:
        """返回指定运行的元数据。

        Args:
            run_id: 运行标识（目录名）。

        Returns:
            运行元数据字典，包含 book、agent、scenario、run_id、model、timestamp。
            如果 run 不存在则返回 None。
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        return {
            "book": run.book,
            "agent": run.agent,
            "scenario": run.scenario,
            "run_id": run.run_id,
            "model": run.model,
            "timestamp": run.timestamp,
        }

    def get_case(self, run_id: str, case_id: str) -> dict[str, Any] | None:
        """返回指定运行下用例的 JSON 数据。

        Args:
            run_id: 运行标识（目录名）。
            case_id: 用例 ID，如 "L1-001"。

        Returns:
            用例的字典数据。如果 run 或 case 不存在则返回 None。
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        case_file = run.dir_path / "cases" / f"{case_id}.json"
        if not case_file.exists():
            return None
        return self._load_json(case_file)

    def get_case_messages(self, run_id: str, case_id: str) -> str | None:
        """返回指定运行下用例的 messages.md 内容。

        Args:
            run_id: 运行标识（目录名）。
            case_id: 用例 ID，如 "L1-001"。

        Returns:
            messages.md 的文本内容。如果文件不存在则返回 None。
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        messages_file = run.dir_path / "cases" / case_id / "messages.md"
        if not messages_file.exists():
            return None
        try:
            return messages_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("读取 messages.md 失败: %s (%s)", messages_file, e)
            return None

    def get_case_messages_jsonl(self, run_id: str, case_id: str) -> list[dict] | None:
        """返回指定运行下用例的 messages.jsonl 解析结果。

        Args:
            run_id: 运行标识（目录名）。
            case_id: 用例 ID，如 "L1-001"。

        Returns:
            消息列表。如果文件不存在则返回 None。
        """
        run = self._runs.get(run_id)
        if run is None:
            return None
        jsonl_file = run.dir_path / "cases" / case_id / "messages.jsonl"
        if not jsonl_file.exists():
            return None
        try:
            messages = []
            with open(jsonl_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return messages
        except OSError as e:
            logger.warning("读取 messages.jsonl 失败: %s (%s)", jsonl_file, e)
            return None

    def compare(self, book: str, agents: list[str], scenario: str,
                run_ids: dict[str, str] | None = None) -> dict[str, Any]:
        """多 Agent 对比数据。

        对比指定书籍、场景下多个 Agent 的运行结果，
        生成按 case 维度的得分对比和各 Agent 的维度汇总。

        Args:
            book: 书籍名称。
            agents: Agent 名称列表。
            scenario: 场景名称。
            run_ids: 可选的 agent → run_id 映射。指定时使用对应 run 的数据，
                     未指定则取该 agent 的最新 run。

        Returns:
            对比数据字典，包含 cases 列表和 dimension_summary 汇总。
        """
        # 收集各 agent 的 run 数据（指定 run_id 或取最新 run）
        agent_reports: dict[str, dict[str, Any]] = {}  # agent -> report_data
        agent_run_ids: dict[str, str] = {}
        for agent in agents:
            if run_ids and agent in run_ids:
                # 使用指定的 run_id
                run = self._runs.get(run_ids[agent])
                if run is not None:
                    agent_run_ids[agent] = run.run_id
                    if run.report_data is not None:
                        agent_reports[agent] = run.report_data
            else:
                # 取最新 run（保持原有逻辑）
                agent_runs = [
                    run for run in self._runs.values()
                    if run.book == book and run.agent == agent and run.scenario == scenario
                ]
                if agent_runs:
                    latest = max(agent_runs, key=lambda r: r.timestamp)
                    agent_run_ids[agent] = latest.run_id
                    if latest.report_data is not None:
                        agent_reports[agent] = latest.report_data

        # 收集所有 case_id（取并集）
        all_case_ids: set[str] = set()
        agent_case_scores: dict[str, dict[str, float]] = {}  # agent -> {case_id: score}
        for agent, report in agent_reports.items():
            cases_summary = report.get("cases_summary", [])
            case_map: dict[str, float] = {}
            for c in cases_summary:
                cid = c.get("case_id", "")
                score = c.get("total_score", 0.0)
                if cid:
                    all_case_ids.add(cid)
                    case_map[cid] = score
            agent_case_scores[agent] = case_map

        # 构建 cases 列表
        cases: list[dict[str, Any]] = []
        for cid in sorted(all_case_ids):
            # 取 question（从第一个有该 case 的 agent 获取）
            question = ""
            for report in agent_reports.values():
                for c in report.get("cases_summary", []):
                    if c.get("case_id") == cid:
                        question = c.get("question", "")
                        break
                if question:
                    break

            scores: dict[str, float] = {}
            for agent in agents:
                scores[agent] = agent_case_scores.get(agent, {}).get(cid, 0.0)

            cases.append({
                "case_id": cid,
                "question": question,
                "scores": scores,
                "run_ids": {agent: agent_run_ids.get(agent, "") for agent in agents},
            })

        # 构建 dimension_summary
        dimension_summary: list[dict[str, Any]] = []
        for agent in agents:
            report = agent_reports.get(agent)
            if report:
                dim_scores = report.get("dimension_scores", {})
                total = report.get("total_score", 0.0)
            else:
                dim_scores = {}
                total = 0.0
            dimension_summary.append({
                "agent": agent,
                "dimensions": dim_scores,
                "total_score": total,
            })

        # 从第一个有 dimensions 的 report 获取维度配置
        dimensions_meta: list[dict[str, str]] = []
        for report in agent_reports.values():
            if "dimensions" in report and report["dimensions"]:
                dimensions_meta = report["dimensions"]
                break

        return {
            "cases": cases,
            "dimension_summary": dimension_summary,
            "dimensions": dimensions_meta,
        }

    def trend(self, book: str, agent: str, scenario: str) -> list[dict[str, Any]]:
        """时间趋势数据，含回归检测。

        按时间升序列出所有运行的评分数据，并标注是否为回归（分数较前一次下降）。

        Args:
            book: 书籍名称。
            agent: Agent 名称。
            scenario: 场景名称。

        Returns:
            趋势数据列表，每项含 run_id、model、timestamp、total_score，
            以及 is_regression 标记。
        """
        matched = [
            run for run in self._runs.values()
            if run.book == book and run.agent == agent and run.scenario == scenario
        ]

        # 按时间戳升序
        matched.sort(key=lambda r: r.timestamp)

        trend_data: list[dict[str, Any]] = []
        prev_score: float | None = None

        for run in matched:
            report = run.report_data
            total_score = report.get("total_score", 0.0) if report else 0.0

            # 回归检测：与前一次运行对比，分数下降即为回归
            is_regression = False
            if prev_score is not None and total_score < prev_score:
                is_regression = True

            trend_data.append({
                "run_id": run.run_id,
                "model": run.model,
                "timestamp": run.timestamp,
                "total_score": total_score,
                "pass_rate": report.get("pass_rate", 0.0) if report else 0.0,
                "dimension_scores": report.get("dimension_scores", {}) if report else {},
                "is_regression": is_regression,
            })

            prev_score = total_score

        return trend_data

    def rebuild(self) -> None:
        """重新扫描目录，更新索引。"""
        self._scan()
