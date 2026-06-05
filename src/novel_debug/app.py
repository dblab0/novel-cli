"""FastAPI 应用：路由和中间件。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from novel_debug.session import SessionManager
from novel_debug.tools import ToolRunner

# 静态文件目录
_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Novel Debug", version="0.1.0")

# 全局单例
_runner: ToolRunner | None = None
_session_mgr: SessionManager | None = None


def _get_runner() -> ToolRunner:
    global _runner
    if _runner is None:
        _runner = ToolRunner()
    return _runner


def _get_session_mgr() -> SessionManager:
    global _session_mgr
    if _session_mgr is None:
        _session_mgr = SessionManager()
    return _session_mgr


# ---- 页面 ----


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = _STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/style.css")
async def style():
    css_path = _STATIC_DIR / "style.css"
    return PlainTextResponse(css_path.read_text(encoding="utf-8"), media_type="text/css")


@app.get("/app.js")
async def script():
    js_path = _STATIC_DIR / "app.js"
    return PlainTextResponse(js_path.read_text(encoding="utf-8"), media_type="application/javascript")


# ---- 工具测试（功能一） ----


@app.get("/api/tools")
async def list_tools():
    """返回所有工具的参数定义。"""
    return ToolRunner.list_tools()


@app.get("/api/books")
async def list_books():
    """返回所有已入库的书名列表。"""
    runner = _get_runner()
    try:
        books = await runner.list_books()
        return {"books": books}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取书籍列表失败: {e}")


class ToolCallRequest(BaseModel):
    """工具调用请求。"""

    params: dict[str, Any]
    book: str | None = None


@app.post("/api/tools/{name}/call")
async def call_tool(name: str, req: ToolCallRequest):
    """直接调用工具，返回结果。"""
    runner = _get_runner()
    try:
        result = await runner.call(name, req.params, req.book)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"工具调用失败: {e}")


# ---- 轨迹构建（功能二） ----


class CreateSessionRequest(BaseModel):
    """创建会话请求。"""

    user_input: str
    book: str | None = None


@app.get("/api/sessions")
async def list_sessions():
    """列出所有历史会话。"""
    mgr = _get_session_mgr()
    return mgr.list_sessions()


@app.get("/api/sessions/{session_id}/timeline")
async def get_session_timeline(session_id: str):
    """获取会话的时间线事件。"""
    mgr = _get_session_mgr()
    try:
        events = mgr.get_session_timeline(session_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"session_id": session_id, "events": events}


@app.post("/api/sessions")
async def create_session(req: CreateSessionRequest):
    """创建新会话。"""
    mgr = _get_session_mgr()
    try:
        return mgr.create(req.user_input, req.book)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class AppendToolCallRequest(BaseModel):
    """追加工具调用请求。"""

    tool_name: str
    arguments: dict[str, Any]
    book: str | None = None


@app.post("/api/sessions/{session_id}/tool-call")
async def append_tool_call(session_id: str, req: AppendToolCallRequest):
    """追加一次工具调用到轨迹。"""
    mgr = _get_session_mgr()
    runner = _get_runner()

    # 获取下一个 tc_id
    tc_id = mgr.next_tc_id(session_id)

    # 调用工具
    try:
        result = await runner.call(req.tool_name, req.arguments, req.book)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"工具调用失败: {e}")

    # 写入轨迹
    arguments_str = json.dumps(req.arguments, ensure_ascii=False)
    mgr.append_tool_call(session_id, tc_id, req.tool_name, arguments_str, result)

    return {
        "tool_call_id": tc_id,
        "result": result,
    }


@app.post("/api/sessions/{session_id}/undo")
async def undo_last(session_id: str):
    """撤回最后一次工具调用。"""
    mgr = _get_session_mgr()
    result = mgr.undo_last(session_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


class AppendTextRequest(BaseModel):
    """追加文本请求。"""

    text: str


@app.post("/api/sessions/{session_id}/text")
async def append_text(session_id: str, req: AppendTextRequest):
    """追加用户粘贴的模型回答。"""
    mgr = _get_session_mgr()
    mgr.append_text(session_id, req.text)
    return {"success": True}


@app.post("/api/sessions/{session_id}/end")
async def end_turn(session_id: str):
    """结束当前轮次。"""
    mgr = _get_session_mgr()
    mgr.end_turn(session_id)
    return {"success": True}


@app.get("/api/sessions/{session_id}/wire")
async def get_wire(session_id: str):
    """导出 wire.jsonl 内容。"""
    mgr = _get_session_mgr()
    try:
        content = mgr.get_wire(session_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return PlainTextResponse(content, media_type="application/jsonl")


@app.get("/api/sessions/{session_id}/copy")
async def copy_trajectory(session_id: str):
    """返回可复制的轨迹文本摘要。"""
    mgr = _get_session_mgr()
    try:
        text = mgr.get_copy_text(session_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"text": text}


@app.get("/api/sessions/{session_id}/copy-with-tools")
async def copy_trajectory_with_tools(session_id: str):
    """返回带工具定义的轨迹上下文（供模型预测下一步工具调用）。"""
    mgr = _get_session_mgr()
    try:
        text = mgr.get_copy_text_with_tools(session_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"text": text}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及其目录。"""
    mgr = _get_session_mgr()
    result = mgr.delete(session_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result
