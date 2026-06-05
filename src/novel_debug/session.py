"""会话管理：创建/追加/撤回/导出轨迹。

每个会话存储在 sessions/{session_id}/wire.jsonl 中，实时追加写入。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from novel_debug import wire_format


class SessionManager:
    """管理 sessions 目录下的会话文件。"""

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self.sessions_dir = sessions_dir or Path("sessions")
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._builtin_skills: dict[str, str] = {}
        self._load_builtin_skills()

    @staticmethod
    def _parse_jsonl(text: str) -> list[dict]:
        """健壮地解析 JSONL 文本，跳过空行，容错粘行。

        使用 json.JSONDecoder.raw_decode 逐个提取 JSON 对象，
        即使同一行粘了多个 JSON 也能正确解析。
        """
        decoder = json.JSONDecoder()
        objects: list[dict] = []
        pos = 0
        while pos < len(text):
            # 跳过空白字符
            while pos < len(text) and text[pos] in " \t\n\r":
                pos += 1
            if pos >= len(text):
                break
            try:
                obj, end = decoder.raw_decode(text, pos)
                objects.append(obj)
                pos = end
            except json.JSONDecodeError:
                break
        return objects

    def _load_builtin_skills(self) -> None:
        """加载所有内置 skill 的 SKILL.md 内容到内存。"""
        from novel_cli.skill import get_builtin_skills_dir

        skills_dir = get_builtin_skills_dir()
        if not skills_dir.exists():
            return
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                self._builtin_skills[skill_dir.name] = skill_md.read_text(
                    encoding="utf-8"
                ).strip()

    def _expand_skill_input(self, user_input: str) -> str:
        """展开 /skill:xxx 前缀为 SKILL.md 内容。

        格式：用户参数在前，SKILL.md 全文在后。
        未找到 skill 时抛出 ValueError。

        Args:
            user_input: 原始用户输入。

        Returns:
            展开后的文本。

        Raises:
            ValueError: skill 不存在时。
        """
        if not user_input.startswith("/skill:"):
            return user_input

        rest = user_input[len("/skill:"):]
        parts = rest.split(None, 1)
        if not parts:
            return user_input

        skill_name = parts[0]
        user_args = parts[1] if len(parts) > 1 else ""

        if skill_name not in self._builtin_skills:
            raise ValueError(f"未找到内置 skill: {skill_name}")

        skill_content = self._builtin_skills[skill_name]
        if user_args:
            return f"{user_args}\n\n{skill_content}"
        return skill_content

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / session_id / "wire.jsonl"

    def create(self, user_input: str, book: str | None = None) -> dict:
        """创建新会话，写入 metadata + TurnBegin。

        如果 user_input 以 /skill: 开头，自动展开为 SKILL.md 内容。

        Returns:
            包含 session_id 的字典。

        Raises:
            ValueError: skill 不存在时。
        """
        expanded_input = self._expand_skill_input(user_input)
        is_skill = user_input != expanded_input

        session_id = f"manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        wire_path = session_dir / "wire.jsonl"
        original_input = user_input if is_skill else None
        lines = [
            json.dumps(wire_format.make_metadata(), ensure_ascii=False),
            json.dumps(
                wire_format.make_turn_begin(expanded_input, original_input=original_input),
                ensure_ascii=False,
            ),
        ]
        wire_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        return {"session_id": session_id, "user_input": user_input, "book": book}

    def append_tool_call(
        self, session_id: str, tc_id: str, tool_name: str, arguments: str, result: dict
    ) -> None:
        """追加 ToolCall + ToolResult。"""
        wire_path = self._session_path(session_id)
        lines = [
            json.dumps(wire_format.make_tool_call(tc_id, tool_name, arguments), ensure_ascii=False),
            json.dumps(wire_format.make_tool_result(tc_id, result), ensure_ascii=False),
        ]
        with open(wire_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def undo_last(self, session_id: str) -> dict:
        """撤回最后一个操作（ToolCall+ToolResult / ContentPart / TurnEnd）。

        Returns:
            操作结果（成功/失败信息）。
        """
        wire_path = self._session_path(session_id)
        if not wire_path.exists():
            return {"success": False, "message": "会话不存在"}

        raw = wire_path.read_text(encoding="utf-8")
        objects = self._parse_jsonl(raw)
        if len(objects) < 2:
            return {"success": False, "message": "没有可撤回的内容"}

        last = objects[-1]
        last_type = last.get("message", {}).get("type")

        # 情况一：最后一个是 ToolResult，倒数第二个是 ToolCall → 撤回配对
        if last_type == "ToolResult":
            second_last = objects[-2]
            if second_last.get("message", {}).get("type") == "ToolCall":
                remaining = objects[:-2]
                wire_path.write_text(
                    "\n".join(json.dumps(o, ensure_ascii=False) for o in remaining) + "\n",
                    encoding="utf-8",
                )
                tc_id = second_last["message"]["payload"]["id"]
                return {"success": True, "message": "已撤回工具调用", "tc_id": tc_id}
            return {"success": False, "message": "ToolResult 前一条不是 ToolCall，无法撤回"}

        # 情况二：最后一个是 ContentPart → 撤回模型回答
        if last_type == "ContentPart":
            remaining = objects[:-1]
            wire_path.write_text(
                "\n".join(json.dumps(o, ensure_ascii=False) for o in remaining) + "\n",
                encoding="utf-8",
            )
            return {"success": True, "message": "已撤回模型回答"}

        # 情况三：最后一个是 TurnEnd → 撤回轮次结束
        if last_type == "TurnEnd":
            remaining = objects[:-1]
            wire_path.write_text(
                "\n".join(json.dumps(o, ensure_ascii=False) for o in remaining) + "\n",
                encoding="utf-8",
            )
            return {"success": True, "message": "已撤回轮次结束"}

        return {"success": False, "message": "无法撤回该类型的内容"}

    def append_text(self, session_id: str, text: str) -> None:
        """追加 ContentPart(text) 消息。"""
        wire_path = self._session_path(session_id)
        line = json.dumps(wire_format.make_text_part(text), ensure_ascii=False)
        with open(wire_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def end_turn(self, session_id: str) -> None:
        """写入 TurnEnd。"""
        wire_path = self._session_path(session_id)
        line = json.dumps(wire_format.make_turn_end(), ensure_ascii=False)
        with open(wire_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def get_wire(self, session_id: str) -> str:
        """返回完整 wire.jsonl 内容。"""
        wire_path = self._session_path(session_id)
        if not wire_path.exists():
            raise FileNotFoundError(f"会话不存在: {session_id}")
        return wire_path.read_text(encoding="utf-8")

    def get_copy_text(self, session_id: str) -> str:
        """返回人类可读的轨迹摘要（供发给外部模型）。"""
        wire_path = self._session_path(session_id)
        if not wire_path.exists():
            raise FileNotFoundError(f"会话不存在: {session_id}")

        raw = wire_path.read_text(encoding="utf-8")
        objects = self._parse_jsonl(raw)
        parts: list[str] = []

        for obj in objects:
            msg_type = obj.get("type") or obj.get("message", {}).get("type")

            if msg_type == "metadata":
                continue
            elif msg_type == "TurnBegin":
                payload = obj["message"]["payload"]
                # 复制轨迹用于外部模型复现，使用展开后的完整内容
                user_input = payload.get("user_input", "")
                parts.append(f"用户问题：{user_input}\n")
            elif msg_type == "ToolCall":
                payload = obj["message"]["payload"]
                name = payload["function"]["name"]
                args = payload["function"]["arguments"]
                parts.append(f"[工具调用] {name}({args})")
            elif msg_type == "ToolResult":
                rv = obj["message"]["payload"]["return_value"]
                output = rv.get("output", "")
                if isinstance(output, list):
                    output = " ".join(
                        p.get("text", "") for p in output if isinstance(p, dict)
                    )
                parts.append(f"[工具结果] {output}\n")
            elif msg_type == "ContentPart":
                text = obj["message"]["payload"].get("text", "")
                if text:
                    parts.append(f"[模型回答] {text}\n")
            elif msg_type == "TurnEnd":
                parts.append("--- 轮次结束 ---\n")

        return "\n".join(parts) + "\n\n请按工具结果回复用户的问题，如果当前信息不够，就说根据当前工具调用结果无法回答"

    def get_copy_text_with_tools(self, session_id: str) -> str:
        """返回带工具定义的轨迹上下文（供模型预测下一步工具调用）。

        Args:
            session_id: 会话 ID。

        Returns:
            完整的 prompt 文本。

        Raises:
            FileNotFoundError: 会话不存在。
            ValueError: 会话已完成（有 TurnEnd）。
        """
        from novel_debug.tools import ToolRunner

        wire_path = self._session_path(session_id)
        if not wire_path.exists():
            raise FileNotFoundError(f"会话不存在: {session_id}")

        raw = wire_path.read_text(encoding="utf-8")
        objects = self._parse_jsonl(raw)

        # 检查是否已完成
        for obj in objects:
            msg_type = obj.get("type") or obj.get("message", {}).get("type")
            if msg_type == "TurnEnd":
                raise ValueError("会话已完成，无法预测下一步")

        # 组装工具定义（过滤 book 参数）
        tools_section_parts: list[str] = ["## 可用工具\n"]
        for tool_info in ToolRunner.list_tools():
            name = tool_info["name"]
            desc = tool_info["description"]
            schema = tool_info["params_schema"]
            props = {
                k: v for k, v in schema.get("properties", {}).items() if k != "book"
            }

            tools_section_parts.append(f"### {name}\n{desc}\n")
            if props:
                tools_section_parts.append("参数:")
                for pname, pinfo in props.items():
                    # 提取类型
                    ptype = pinfo.get("type")
                    if not ptype:
                        for sub in pinfo.get("anyOf", []):
                            if sub.get("type") and sub["type"] != "null":
                                ptype = sub["type"]
                                break
                    ptype = ptype or "?"
                    pdesc = pinfo.get("description", "")
                    tools_section_parts.append(f"  - {pname} ({ptype}): {pdesc}")
                tools_section_parts.append("")

        # 组装已有对话
        dialog_parts: list[str] = ["## 已有对话\n"]
        for obj in objects:
            msg_type = obj.get("type") or obj.get("message", {}).get("type")
            if msg_type == "TurnBegin":
                payload = obj["message"]["payload"]
                user_input = payload.get("user_input", "")
                dialog_parts.append(f"用户问题：{user_input}\n")
            elif msg_type == "ToolCall":
                payload = obj["message"]["payload"]
                func = payload["function"]
                dialog_parts.append(f"[工具调用] {func['name']}({func['arguments']})")
            elif msg_type == "ToolResult":
                rv = obj["message"]["payload"]["return_value"]
                output = rv.get("output", "")
                if isinstance(output, list):
                    output = " ".join(
                        p.get("text", "") for p in output if isinstance(p, dict)
                    )
                dialog_parts.append(f"[工具结果] {output}\n")

        # 组装任务指令
        task_section = (
            "## 任务\n"
            "\n"
            "根据以上对话上下文，预测下一步应该调用的工具和参数。\n"
            "只返回一行，格式：ToolName(param1=\"value1\", param2=\"value2\")\n"
            "始终预测下一步最合理的工具调用，不要返回 NO_TOOL_CALL。"
        )

        return "\n".join(tools_section_parts) + "\n" + "\n".join(dialog_parts) + "\n" + task_section

    def list_sessions(self) -> list[dict]:
        """列出所有会话的摘要信息。"""
        result = []
        if not self.sessions_dir.exists():
            return result

        for d in sorted(self.sessions_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            wire_path = d / "wire.jsonl"
            if not wire_path.exists():
                continue

            session_id = d.name
            user_input = ""
            tool_count = 0
            has_turn_end = False

            for obj in self._parse_jsonl(wire_path.read_text(encoding="utf-8")):
                msg_type = obj.get("type") or obj.get("message", {}).get("type")
                if msg_type == "TurnBegin":
                    payload = obj["message"]["payload"]
                    user_input = payload.get("original_input") or payload.get("user_input", "")
                elif msg_type == "ToolCall":
                    tool_count += 1
                elif msg_type == "TurnEnd":
                    has_turn_end = True

            result.append({
                "session_id": session_id,
                "user_input": user_input[:80],
                "tool_count": tool_count,
                "completed": has_turn_end,
            })
        return result

    def delete(self, session_id: str) -> dict:
        """删除会话及其目录。

        Args:
            session_id: 会话 ID。

        Returns:
            操作结果。
        """
        import shutil

        session_dir = self.sessions_dir / session_id
        if not session_dir.exists() or not session_dir.is_dir():
            return {"success": False, "message": "会话不存在"}

        shutil.rmtree(session_dir)
        return {"success": True, "message": "已删除"}

    def get_session_timeline(self, session_id: str) -> list[dict]:
        """解析 wire.jsonl 返回时间线事件列表。"""
        wire_path = self._session_path(session_id)
        if not wire_path.exists():
            raise FileNotFoundError(f"会话不存在: {session_id}")

        events = []
        for obj in self._parse_jsonl(wire_path.read_text(encoding="utf-8")):
            msg = obj.get("message", {})
            msg_type = msg.get("type")
            payload = msg.get("payload", {})

            if msg_type == "TurnBegin":
                display_text = payload.get("original_input") or payload.get("user_input", "")
                events.append({"type": "user", "label": "用户", "content": display_text})
            elif msg_type == "ToolCall":
                func = payload.get("function", {})
                events.append({
                    "type": "tool-call",
                    "label": f"{func.get('name', '')}({func.get('arguments', '')})",
                    "content": "",
                })
            elif msg_type == "ToolResult":
                rv = payload.get("return_value", {})
                output = rv.get("output", "")
                if isinstance(output, list):
                    output = "\n".join(p.get("text", "") for p in output if isinstance(p, dict))
                is_error = rv.get("is_error", False)
                events.append({
                    "type": f"tool-result{' is-error' if is_error else ''}",
                    "label": f"→ {output}",
                    "content": "",
                })
            elif msg_type == "ContentPart":
                text = payload.get("text", "")
                if text:
                    events.append({
                        "type": "text-part",
                        "label": "模型回答",
                        "content": text,
                    })

        return events

    def next_tc_id(self, session_id: str) -> str:
        """获取下一个 tool_call id（基于当前文件中的最大序号）。"""
        wire_path = self._session_path(session_id)
        if not wire_path.exists():
            return "tc_1"

        max_id = 0
        for obj in self._parse_jsonl(wire_path.read_text(encoding="utf-8")):
            msg = obj.get("message", {})
            if msg.get("type") == "ToolCall":
                tc_id = msg["payload"]["id"]
                if tc_id.startswith("tc_"):
                    try:
                        num = int(tc_id[3:])
                        max_id = max(max_id, num)
                    except ValueError:
                        pass

        return f"tc_{max_id + 1}"
