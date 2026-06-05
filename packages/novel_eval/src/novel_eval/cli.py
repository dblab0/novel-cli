"""Novel Eval CLI 入口。

提供五个子命令：
- generate: 生成测试用例集
- run: 执行评估
- extract-context: 从 session 目录提取上下文消息
- regen-report: 从已有 cases 目录重新生成报告
- view: 启动 Eval Viewer Web 服务器

使用方式：
    novel-eval generate --config config.yaml --task tool_usage --book 凡人修仙传
    novel-eval run eval_cases/凡人修仙传/level_1_entity.yaml
    novel-eval run eval_cases/凡人修仙传/level_*.yaml
    novel-eval run --skip-run --result-dir eval_results/凡人修仙传/novel-v3/level_1_entity/minimax-m2.7_2026-04-26_123456/
    novel-eval extract-context --result-dir eval_results/凡人修仙传/novel-v3/level_1_entity/minimax-m2.7_2026-04-26_123456/
    novel-eval regen-report --result-dir eval_results/凡人修仙传/novel-v3/level_1_entity/minimax-m2.7_2026-04-26_123456/
    novel-eval view --eval-dir eval_results --port 8080
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from novel_eval.config import load_config
from novel_eval.context import ContextExtractor
from novel_eval.models import CaseResult, EvalScore, load_scored_results_from_dir
from novel_eval.tasks.registry import get_task_cls
from novel_eval.tasks.tool_usage.models import EvalConfig
from novel_eval.report import generate_report
from novel_eval.tasks.tool_usage import get_scenario_filename

app = typer.Typer(
    name="novel-eval",
    help="Novel Agent 工具使用评估框架",
    add_completion=False,
)


def _assert_result_dir(result_dir: Path | None) -> Path:
    """校验 --result-dir 参数，skip-run / rerun-missing 模式下强制必填。

    Args:
        result_dir: 用户传入的结果目录路径。

    Returns:
        已验证存在的结果目录路径。

    Raises:
        typer.Exit: 未指定或目录不存在时退出。
    """
    if result_dir is None:
        typer.echo("此模式必须指定 --result-dir")
        raise typer.Exit(1)
    if not result_dir.exists():
        typer.echo(f"结果目录不存在: {result_dir}")
        raise typer.Exit(1)
    return result_dir


def _extract_agent_version(config: EvalConfig) -> str:
    """从 agent_file 路径提取 agent 版本名（父目录名）。

    Args:
        config: 评估配置对象。

    Returns:
        agent 版本名字符串，如 'novel-v3'。

    Raises:
        ValueError: agent_file 无父目录时报错。
    """
    agent_file = Path(config.runner.agent_file)
    version = agent_file.parent.name
    if not version or version == agent_file.name:
        raise ValueError(
            f"agent_file 路径缺少父目录，无法提取版本名: {config.runner.agent_file}"
        )
    return version


def _extract_model_slug(config: EvalConfig) -> str:
    """从 runner.eval_model 提取文件名安全的模型标识。

    取最后一个 '/' 后面的部分；为空时回退为 'default'。

    Args:
        config: 评估配置对象。

    Returns:
        模型标识字符串，如 'minimax-m2.7'。
    """
    model = config.runner.eval_model
    slug = model.rsplit("/", 1)[-1] if model else "default"
    return slug or "default"


def _resolve_yaml_paths(yaml_paths: list[Path]) -> list[Path]:
    """校验 YAML 文件路径列表，确保所有文件均存在。

    Args:
        yaml_paths: YAML 文件路径列表。

    Returns:
        原样返回已校验的路径列表。

    Raises:
        typer.Exit: 文件不存在时退出。
    """
    for p in yaml_paths:
        if not p.exists():
            typer.echo(f"YAML 文件不存在: {p}")
            raise typer.Exit(1)
    return yaml_paths


def _load_book_from_yaml(yaml_path: Path) -> str:
    """从 YAML 用例文件中读取书名。

    Args:
        yaml_path: YAML 文件路径。

    Returns:
        书名字符串。
    """
    import yaml as yaml_lib

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml_lib.safe_load(f)
    return data["book"]


def _run_normal(task_obj: Any, yaml_path: Path, output: Path, config: EvalConfig) -> Path:
    """正常模式：加载 YAML → 执行评估 → 提取 context → 生成报告。

    Args:
        task_obj: 评估任务对象。
        yaml_path: YAML 用例文件路径。
        output: 结果输出根目录。
        config: 评估配置对象。

    Returns:
        报告目录路径。
    """
    book = _load_book_from_yaml(yaml_path)
    agent_version = _extract_agent_version(config)

    typer.echo(f"加载用例: {yaml_path}")
    cases = task_obj.load_cases(yaml_path)
    typer.echo(f"加载了 {len(cases)} 个用例")

    typer.echo("开始评估...")
    results: list[tuple[CaseResult, EvalScore]] = asyncio.run(task_obj.evaluate(cases))
    typer.echo(f"评估完成，生成了 {len(results)} 个评分")

    yaml_stem = yaml_path.stem  # 例如 "level_1_entity"
    model_slug = _extract_model_slug(config)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    report_dir = output / book / agent_version / yaml_stem / f"{model_slug}_{timestamp}"
    generate_report(
        results, report_dir,
        task_type=task_obj.name, dimensions=task_obj.dimensions,
    )

    # 自动提取 context
    typer.echo("提取上下文消息...")
    extractor = ContextExtractor(task_obj.runner.sessions_dir)
    case_ids = [c.id for c in cases]
    extract_results = extractor.extract_batch(case_ids, report_dir / "cases")
    success_count = sum(1 for v in extract_results.values() if v)
    typer.echo(f"上下文提取完成：成功 {success_count}/{len(case_ids)}")

    return report_dir


def _run_skip(task_obj: Any, yaml_path: Path, result_dir: Path) -> Path:
    """skip-run 模式：加载已有结果 → Judge 评分 → 覆写报告。

    Args:
        task_obj: 评估任务对象。
        yaml_path: YAML 用例文件路径。
        result_dir: 已有评估结果目录（用于输出覆写）。

    Returns:
        报告目录路径（与 result_dir 相同）。
    """
    typer.echo(f"加载用例: {yaml_path}")
    cases = task_obj.load_cases(yaml_path)
    typer.echo(f"加载了 {len(cases)} 个用例")

    # 使用 BaseTask 钩子加载已有结果
    typer.echo("加载已有执行结果...")
    case_results = task_obj.load_existing_results(cases, result_dir)
    typer.echo(f"加载了 {len(case_results)} 个已有结果")

    typer.echo("开始 Judge 评分...")
    results: list[tuple[CaseResult, EvalScore]] = asyncio.run(
        task_obj.evaluate(cases, skip_run=True, existing_results=case_results)
    )
    typer.echo(f"评分完成，生成了 {len(results)} 个评分")

    generate_report(
        results, result_dir,
        task_type=task_obj.name, dimensions=task_obj.dimensions,
    )
    return result_dir


def _run_rerun_missing(task_obj: Any, yaml_path: Path, result_dir: Path) -> Path:
    """rerun-missing 模式：检测缺失 → 重跑 → 合并 → 覆写。

    Args:
        task_obj: 评估任务对象。
        yaml_path: YAML 用例文件路径。
        result_dir: 已有评估结果目录。

    Returns:
        报告目录路径（与 result_dir 相同）。
    """
    typer.echo(f"加载用例: {yaml_path}")
    cases = task_obj.load_cases(yaml_path)
    typer.echo(f"加载了 {len(cases)} 个用例")

    # 使用 BaseTask 钩子检测缺失
    missing_cases = task_obj.detect_missing(cases)
    if not missing_cases:
        typer.echo("所有用例均已存在，无需重跑")
        raise typer.Exit(0)

    missing_ids = [c.id for c in missing_cases]
    typer.echo(f"检测到 {len(missing_cases)} 个缺失用例: {', '.join(missing_ids)}")

    # 加载已有评分结果
    typer.echo(f"从 {result_dir} 加载已有评分结果...")
    existing_scored = load_scored_results_from_dir(result_dir)
    existing_map = {cr.case_id: (cr, es) for cr, es in existing_scored}
    typer.echo(f"加载了 {len(existing_scored)} 个已有结果")

    # 重跑缺失用例 + Judge 评分
    typer.echo("开始重跑缺失用例并评分...")
    new_scored = asyncio.run(task_obj.evaluate(missing_cases))
    typer.echo(f"重跑并评分完成，共 {len(new_scored)} 个用例")

    # 合并结果：按 YAML 用例原始顺序排列
    new_scored_map = {cr.case_id: (cr, es) for cr, es in new_scored}
    results: list[tuple[CaseResult, EvalScore]] = []
    for case in cases:
        if case.id in new_scored_map:
            results.append(new_scored_map[case.id])
        elif case.id in existing_map:
            results.append(existing_map[case.id])
    typer.echo(f"合并完成，共 {len(results)} 个用例结果")

    generate_report(
        results, result_dir,
        task_type=task_obj.name, dimensions=task_obj.dimensions,
    )
    return result_dir


@app.command()
def generate(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="配置文件路径")
    ] = None,
    task: Annotated[
        str, typer.Option("--task", "-t", help="任务类型")
    ] = "tool_usage",
    book: Annotated[
        str, typer.Option("--book", "-b", help="书名")
    ] = "凡人修仙传",
    output: Annotated[
        Path, typer.Option("--output", "-o", help="输出目录")
    ] = Path("eval_cases"),
    scenario: Annotated[
        str, typer.Option("--scenario", "-s", help="场景名称")
    ] = "level_1_entity",
    instruction: Annotated[
        str | None, typer.Option("--instruction", "-i", help="自定义指令，引导问题生成方向")
    ] = None,
    seed: Annotated[
        int | None, typer.Option("--seed", help="随机种子，用于确保可复现")
    ] = None,
    samples: Annotated[
        int | None, typer.Option("--samples", help="每种类型抽取数量（skill_generation 使用）")
    ] = None,
    append: Annotated[
        bool, typer.Option("--append", "-a", help="追加模式，向已有文件追加而非覆盖")
    ] = False,
) -> None:
    """生成测试用例集。

    从设定文件生成测试问题，保存为 YAML 格式。

    Args:
        config: 配置文件路径。
        task: 任务类型，默认 tool_usage。
        book: 书名，默认 凡人修仙传。
        output: 输出目录，默认 eval_cases。
        scenario: 场景名称，默认 level_1_entity。
        instruction: 自定义指令，引导问题生成方向（如"集中在人物关系"）。
        seed: 随机种子，用于确保可复现。
        samples: 每种类型抽取数量（skill_generation 使用）。
        append: 追加模式，向已有 YAML 文件追加用例而非覆盖。
    """
    cfg = load_config(config)
    settings_dir = Path(cfg.settings_dir)

    # 使用 registry 获取 task 类
    try:
        task_cls = get_task_cls(task)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    task_obj = task_cls(cfg)

    if task == "tool_usage":
        # tool_usage 特有的 scenario 和 append 逻辑
        typer.echo(f"生成 {book} 场景 {scenario} 测试用例...")

        existing_cases = None
        id_offset = 0
        if append:
            yaml_path = output / book / get_scenario_filename(scenario)
            if yaml_path.exists():
                existing_cases = task_obj.load_cases(yaml_path)
                id_offset = len(existing_cases)
                typer.echo(f"追加模式：已有 {id_offset} 个用例，新用例从 L{scenario}-{id_offset + 1:03d} 起编号")

        cases = task_obj.generate_cases(
            book, settings_dir, scenario=scenario,
            instruction=instruction, id_offset=id_offset,
        )

        if cases:
            task_obj.save_cases(output, cases, book, scenario, existing_cases=existing_cases)
            total = len(existing_cases or []) + len(cases)
            if append and existing_cases:
                typer.echo(f"追加 {len(cases)} 个用例，文件现有 {total} 个用例：{output / book}")
            else:
                typer.echo(f"已保存 {len(cases)} 个用例到 {output / book}")
        else:
            typer.echo("未生成任何用例（请检查 LLM 配置是否正确）")

    elif task == "skill_generation":
        # skill_generation 特有的 CSV 数据抽取逻辑
        typer.echo(f"从 CSV 抽取 {book} 实体生成技能评估用例...")

        cases = task_obj.generate_cases(
            book,
            seed=seed,
            samples_per_type=samples,
        )

        if cases:
            saved_paths = task_obj.save_cases(output, cases, book, append=append)
            if append:
                typer.echo(f"追加 {len(cases)} 个用例到 {len(saved_paths)} 个文件")
            else:
                typer.echo(f"已保存 {len(cases)} 个用例到 {len(saved_paths)} 个文件：")
                for p in saved_paths:
                    typer.echo(f"  - {p}")
        else:
            typer.echo("未生成任何用例（请检查 CSV 数据路径是否正确）")


@app.command()
def run(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="配置文件路径")
    ] = None,
    task: Annotated[
        str, typer.Option("--task", "-t", help="任务类型")
    ] = "tool_usage",
    yaml: Annotated[
        list[Path], typer.Argument(help="YAML 用例文件路径，支持多文件")
    ] = [],
    output: Annotated[
        Path, typer.Option("--output", "-o", help="结果输出目录")
    ] = Path("eval_results"),
    skip_run: Annotated[
        bool, typer.Option("--skip-run", help="跳过 agent 执行，仅进行 Judge 评分")
    ] = False,
    result_dir: Annotated[
        Path | None, typer.Option("--result-dir", "-r", help="已有评估结果目录（skip-run/rerun-missing 模式必填）")
    ] = None,
    rerun_missing: Annotated[
        bool, typer.Option("--rerun-missing", help="重跑缺失用例：自动检测 sessions 中缺 wire.jsonl 的用例并重跑")
    ] = False,
) -> None:
    """执行评估。

    加载 YAML 用例，执行评估，生成报告。

    Args:
        config: 配置文件路径。
        task: 任务类型，默认 tool_usage。
        yaml: YAML 用例文件路径，支持多文件。
        output: 结果输出目录，默认 eval_results。
        skip_run: 跳过 agent 执行阶段，仅进行 Judge 评分。
        result_dir: 已有评估结果目录（skip-run/rerun-missing 模式必填，从中加载 CaseResult）。
        rerun_missing: 重跑缺失用例，自动检测 sessions 中缺 wire.jsonl 的用例并重跑。
    """
    cfg = load_config(config)

    # 使用 registry 获取 task 类
    try:
        task_cls = get_task_cls(task)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    task_obj = task_cls(cfg)

    # 统一模式分发，无 task 类型判断
    if skip_run:
        verified_dir = _assert_result_dir(result_dir)
        if not yaml:
            typer.echo("skip-run 模式需要指定 YAML 用例文件路径")
            raise typer.Exit(1)
        yaml_path = yaml[0]
        if not yaml_path.exists():
            typer.echo(f"YAML 文件不存在: {yaml_path}")
            raise typer.Exit(1)
        report_dir = _run_skip(task_obj, yaml_path, verified_dir)
        report_dirs = [report_dir]
    elif rerun_missing:
        if not task_obj.supports_rerun_missing:
            typer.echo(f"任务 {task_obj.name} 不支持 rerun-missing 模式")
            raise typer.Exit(1)
        verified_dir = _assert_result_dir(result_dir)
        if not yaml:
            typer.echo("rerun-missing 模式需要指定 YAML 用例文件路径")
            raise typer.Exit(1)
        yaml_path = yaml[0]
        if not yaml_path.exists():
            typer.echo(f"YAML 文件不存在: {yaml_path}")
            raise typer.Exit(1)
        report_dir = _run_rerun_missing(task_obj, yaml_path, verified_dir)
        report_dirs = [report_dir]
    else:
        if not yaml:
            typer.echo("请指定 YAML 用例文件路径")
            raise typer.Exit(1)
        yaml_paths = _resolve_yaml_paths(yaml)
        report_dirs: list[Path] = []
        for i, yp in enumerate(yaml_paths, 1):
            if len(yaml_paths) > 1:
                typer.echo(f"\n=== [{i}/{len(yaml_paths)}] {yp.name} ===")
            report_dir = _run_normal(task_obj, yp, output, cfg)
            report_dirs.append(report_dir)
        if len(yaml_paths) > 1:
            typer.echo(f"\n全部完成，共处理 {len(yaml_paths)} 个文件")

    if len(report_dirs) == 1:
        typer.echo(f"报告已保存到: {report_dirs[0]}")
    else:
        for rd in report_dirs:
            typer.echo(f"  - {rd}")


@app.command()
def extract_context(
    result_dir: Annotated[
        Path, typer.Option("--result-dir", "-r", help="包含 cases/ 目录的评估结果目录")
    ],
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="配置文件路径（用于解析 sessions_dir）")
    ] = None,
) -> None:
    """从 session 目录提取上下文消息。

    读取 result_dir/cases/*.json 获取 case_id 列表，从对应的 session 目录
    提取 context.jsonl，过滤内部状态，生成 messages.jsonl 和 messages.md。

    Args:
        result_dir: 包含 cases/ 子目录的评估结果目录。
        config: 配置文件路径，用于解析 sessions_dir。
    """
    if not result_dir.exists():
        typer.echo(f"结果目录不存在: {result_dir}")
        raise typer.Exit(1)

    cases_dir = result_dir / "cases"
    if not cases_dir.exists():
        typer.echo(f"cases 目录不存在: {cases_dir}")
        raise typer.Exit(1)

    # 加载配置以获取 sessions_dir
    cfg = load_config(config)

    # 导入 runner 以获取 sessions_dir
    from novel_eval.runner import resolve_sessions_dir

    work_dir = Path(cfg.runner.work_dir).expanduser()
    sessions_dir = resolve_sessions_dir(work_dir)

    typer.echo(f"Session 目录: {sessions_dir}")

    # 获取所有 case_id（从 *.json 文件名提取）
    json_files = list(cases_dir.glob("*.json"))
    if not json_files:
        typer.echo(f"cases 目录中没有 JSON 文件: {cases_dir}")
        raise typer.Exit(1)

    case_ids = [f.stem for f in json_files]
    typer.echo(f"找到 {len(case_ids)} 个用例：{', '.join(case_ids[:5])}{'...' if len(case_ids) > 5 else ''}")

    # 提取上下文
    typer.echo("开始提取上下文消息...")
    extractor = ContextExtractor(sessions_dir)
    extract_results = extractor.extract_batch(case_ids, cases_dir)

    # 统计结果
    success_count = sum(1 for v in extract_results.values() if v)
    failed_ids = [case_id for case_id, success in extract_results.items() if not success]

    typer.echo(f"\n提取完成：成功 {success_count}/{len(case_ids)}")
    if failed_ids:
        typer.echo(f"跳过（context.jsonl 不存在）：{', '.join(failed_ids)}")

    typer.echo(f"\n输出目录: {cases_dir}")
    for case_id in case_ids:
        if extract_results[case_id]:
            typer.echo(f"  - {case_id}/messages.jsonl + messages.md")


@app.command()
def regen_report(
    result_dir: Annotated[
        Path, typer.Option("--result-dir", "-r", help="包含 cases/ 目录的评估结果目录")
    ],
) -> None:
    """从已有 cases 目录重新生成报告。

    读取 result_dir/cases/*.json，重新生成 report.json 和 report.md。
    用于修改报告逻辑后无需重跑评估即可更新报告。

    Args:
        result_dir: 包含 cases/ 子目录的评估结果目录。
    """
    if not result_dir.exists():
        typer.echo(f"结果目录不存在: {result_dir}")
        raise typer.Exit(1)

    cases_dir = result_dir / "cases"
    if not cases_dir.exists():
        typer.echo(f"cases 目录不存在: {cases_dir}")
        raise typer.Exit(1)

    if not list(cases_dir.glob("*.json")):
        typer.echo(f"cases 目录中没有 JSON 文件: {cases_dir}")
        raise typer.Exit(1)

    typer.echo(f"从 {cases_dir} 加载评估结果...")
    results = load_scored_results_from_dir(result_dir)
    typer.echo(f"加载了 {len(results)} 个用例结果")

    # 从旧 report.json 读取 task_type 和 dimensions
    old_report_path = result_dir / "report.json"
    task_type = "tool_usage"
    dimensions = None
    if old_report_path.exists():
        import json
        try:
            old_report = json.loads(old_report_path.read_text(encoding="utf-8"))
            task_type = old_report.get("task_type", "tool_usage")
            dimensions = old_report.get("dimensions")
        except (json.JSONDecodeError, OSError):
            pass

    generate_report(results, result_dir, task_type=task_type, dimensions=dimensions)
    typer.echo(f"报告已重新生成到: {result_dir}")


@app.command()
def view(
    port: Annotated[int, typer.Option("--port", help="服务端口")] = 8080,
    host: Annotated[str, typer.Option("--host", help="监听地址")] = "127.0.0.1",
    eval_dir: Annotated[Path, typer.Option("--eval-dir", help="评估结果目录")] = Path("./eval_results"),
    no_open: Annotated[bool, typer.Option("--no-open", help="不自动打开浏览器")] = False,
    dev: Annotated[bool, typer.Option("--dev", help="开发模式，跳过静态文件挂载")] = False,
) -> None:
    """启动 Eval Viewer Web 服务器。

    提供可视化界面浏览评估结果，支持按书籍 / Agent / 场景维度查看报告，
    以及多 Agent 对比和时间趋势分析。

    Args:
        port: 服务端口，默认 8080。
        host: 监听地址，默认 127.0.0.1。
        eval_dir: 评估结果目录，默认 ./eval_results。
        no_open: 不自动打开浏览器。
        dev: 开发模式，跳过静态文件挂载（前端由独立 dev server 提供）。
    """
    from novel_eval.viewer.server import run_viewer_server

    run_viewer_server(
        host=host,
        port=port,
        eval_dir=eval_dir,
        open_browser=not no_open,
        dev=dev,
    )


def main() -> None:
    """CLI 主入口。"""
    app()


if __name__ == "__main__":
    main()
