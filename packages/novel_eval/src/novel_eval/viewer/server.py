"""Eval Viewer FastAPI 应用。

提供 Web 界面用于浏览评估结果，包含 REST API 端点和 SPA 静态文件服务。

主要功能：
- 按书籍 / Agent / 场景 / 运行维度浏览评估结果
- 单次运行报告查看
- 用例详情和消息记录查看
- 多 Agent 对比分析
- 时间趋势和回归检测
- 索引重建（热刷新）
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse

from novel_cli.utils.server import (
    find_available_port,
    format_url,
    get_network_addresses,
    print_banner,
)
from novel_eval.viewer.data import EvalDataIndex

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8080
STATIC_DIR = Path(__file__).parent / "static"


def create_app(eval_dir: Path | None = None, dev: bool = False) -> FastAPI:
    """创建 Eval Viewer FastAPI 应用。

    Args:
        eval_dir: 评估结果目录路径，默认为当前目录下的 eval_results。
        dev: 是否为开发模式（跳过静态文件挂载）。

    Returns:
        配置完成的 FastAPI 应用实例。
    """
    if eval_dir is None:
        eval_dir = Path("eval_results")
    eval_dir = Path(eval_dir)

    # 构建数据索引
    index = EvalDataIndex(eval_dir)

    # 后台自动重建索引任务
    _reindex_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """应用生命周期：启动后台定时重建索引任务。"""
        nonlocal _reindex_task
        async def _auto_reindex() -> None:
            """每 15 秒重建索引，使新评估结果自动可见。"""
            while True:
                await asyncio.sleep(15)
                index.rebuild()
        _reindex_task = asyncio.create_task(_auto_reindex())
        yield
        if _reindex_task:
            _reindex_task.cancel()

    application = FastAPI(title="Novel Eval Viewer", lifespan=lifespan)
    application.state.index = index

    # 添加 GZIP 响应压缩中间件
    application.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    # 添加 CORS 中间件（本地工具，端口动态分配，使用通配符可接受）
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    @application.get("/healthz")
    def health_probe() -> dict[str, Any]:
        """健康检查接口，用于验证服务是否正常运行。"""
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # API 路由
    # ------------------------------------------------------------------

    @application.get("/api/books")
    def list_books() -> list[str]:
        """返回所有书籍名称列表。"""
        return index.list_books()

    @application.get("/api/books/{book:path}/agents")
    def list_agents(book: str) -> list[str]:
        """返回指定书籍下的 Agent 列表。"""
        agents = index.list_agents(book)
        if not agents:
            raise HTTPException(status_code=404, detail=f"书籍不存在或无 Agent 数据: {book}")
        return agents

    @application.get("/api/books/{book:path}/agents/{agent}/scenarios")
    def list_scenarios(book: str, agent: str) -> list[str]:
        """返回指定书籍 + Agent 下的场景列表。"""
        scenarios = index.list_scenarios(book, agent)
        if not scenarios:
            raise HTTPException(status_code=404, detail=f"Agent不存在或无场景数据: {agent}")
        return scenarios

    @application.get("/api/books/{book:path}/agents/{agent}/scenarios/{scenario}/runs")
    def list_runs(book: str, agent: str, scenario: str) -> list[dict]:
        """返回指定条件下的运行批次列表，按时间升序。"""
        runs = index.list_runs(book, agent, scenario)
        if not runs:
            raise HTTPException(status_code=404, detail=f"场景不存在或无运行数据: {scenario}")
        return runs

    @application.get("/api/runs/{run_id:path}/report")
    def get_report(run_id: str) -> dict:
        """返回指定运行的 report.json 数据。"""
        report = index.get_report(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")
        return report

    @application.get("/api/runs/{run_id:path}/cases/{case_id}/messages")
    def get_case_messages(run_id: str, case_id: str) -> dict[str, str]:
        """返回指定用例的 messages.md 内容。"""
        messages = index.get_case_messages(run_id, case_id)
        if messages is None:
            raise HTTPException(status_code=404, detail="消息记录不存在")
        return {"content": messages}

    @application.get("/api/runs/{run_id:path}/cases/{case_id}/messages/jsonl")
    def get_case_messages_jsonl(run_id: str, case_id: str) -> list[dict]:
        """返回指定用例的 messages.jsonl 结构化消息列表。"""
        messages = index.get_case_messages_jsonl(run_id, case_id)
        if messages is None:
            raise HTTPException(status_code=404, detail="消息记录不存在")
        return messages

    @application.get("/api/runs/{run_id:path}/cases/{case_id}")
    def get_case(run_id: str, case_id: str) -> dict:
        """返回指定用例的完整 JSON 数据。"""
        case = index.get_case(run_id, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail=f"Case不存在: {run_id}/{case_id}")
        return case

    @application.get("/api/runs/{run_id:path}/cases")
    def list_cases(run_id: str) -> list[dict]:
        """列出指定 run 下的所有用例摘要。"""
        report = index.get_report(run_id)
        if report is None:
            raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")
        return index.list_cases_for_run(run_id)

    @application.get("/api/runs/{run_id:path}")
    def get_run_info(run_id: str) -> dict:
        """返回指定运行的元数据。"""
        info = index.get_run_info(run_id)
        if info is None:
            raise HTTPException(status_code=404, detail=f"运行不存在: {run_id}")
        return info

    @application.get("/api/compare")
    def compare(request: Request, book: str, agents: str, scenario: str) -> dict:
        """多 Agent 对比数据。

        Query 参数 agents 为逗号分隔的 Agent 名称列表。
        可选 run_id_{agent} 参数指定某 agent 使用的具体 run_id。
        禁止跨 task_type 对比（维度不同无比较意义）。
        """
        agent_list = [a.strip() for a in agents.split(",")]

        # 校验所有 agent + scenario 的 task_type 一致
        task_types: set[str] = set()
        for agent in agent_list:
            runs = [
                run for run in index._runs.values()
                if run.book == book and run.agent == agent and run.scenario == scenario
            ]
            for run in runs:
                task_types.add(run.task_type)

        if len(task_types) > 1:
            raise HTTPException(
                status_code=400,
                detail=f"不能跨任务类型对比: {task_types}",
            )

        # 从 query params 中提取 run_ids 映射
        run_ids: dict[str, str] = {}
        for key, value in request.query_params.items():
            if key.startswith("run_id_"):
                agent_name = key.removeprefix("run_id_")
                run_ids[agent_name] = value

        return index.compare(book, agent_list, scenario,
                             run_ids=run_ids if run_ids else None)

    @application.get("/api/trend")
    def trend(book: str, agent: str, scenario: str) -> list[dict]:
        """时间趋势数据，含回归检测。"""
        return index.trend(book, agent, scenario)

    @application.post("/api/reindex")
    def reindex() -> dict[str, str]:
        """手动触发索引重建。"""
        index.rebuild()
        return {"status": "ok", "message": "索引重建完成"}

    # ------------------------------------------------------------------
    # SPA 静态文件（非开发模式）
    # ------------------------------------------------------------------

    if not dev and STATIC_DIR.exists():
        @application.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            """SPA 路由 fallback：未知路径返回 index.html。"""
            file_path = STATIC_DIR / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(STATIC_DIR / "index.html")

    return application


def run_viewer_server(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    eval_dir: Path | None = None,
    open_browser: bool = True,
    dev: bool = False,
) -> None:
    """运行 Eval Viewer 服务器。

    Args:
        host: 监听地址，默认为本地回环地址。
        port: 端口号，默认为 8080。
        eval_dir: 评估结果目录路径，默认为当前目录下的 eval_results。
        open_browser: 是否自动打开浏览器，默认开启。
        dev: 是否为开发模式（跳过静态文件挂载），默认关闭。
    """
    if eval_dir is None:
        eval_dir = Path("eval_results")

    # 查找可用端口
    actual_port = find_available_port(host, port)

    # 构建 banner
    url = format_url(host, actual_port)
    banner_lines = [
        "<center>Novel Eval Viewer</center>",
        "<hr>",
        f"  地址: {url}",
    ]
    if host == "0.0.0.0":
        for addr in get_network_addresses():
            banner_lines.append(f"  网络: {format_url(addr, actual_port)}")
    banner_lines.append("<hr>")
    banner_lines.append(f"  数据: {eval_dir}")
    banner_lines.append("  按 Ctrl+C 停止服务")
    print_banner(banner_lines)

    # 自动打开浏览器
    if open_browser:
        browser_url = f"http://localhost:{actual_port}" if host == "0.0.0.0" else url

        def open_browser_after_delay() -> None:
            """延迟打开浏览器，等待服务器启动完成。"""
            time.sleep(1.5)
            webbrowser.open(browser_url)

        thread = threading.Thread(target=open_browser_after_delay, daemon=True)
        thread.start()

    # 创建应用实例并启动 uvicorn（不使用 factory 模式以传递 eval_dir 参数）
    app = create_app(eval_dir=eval_dir, dev=dev)
    uvicorn.run(
        app,
        host=host,
        port=actual_port,
        log_level="info",
        timeout_graceful_shutdown=3,
    )
