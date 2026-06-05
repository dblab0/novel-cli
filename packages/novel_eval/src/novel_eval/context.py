"""Context 提取器模块。

从 session 目录的 context.jsonl 中提取有效消息，过滤内部状态信息，
生成 messages.jsonl（机器可读）和 messages.md（人工可读）。
"""

import json
from pathlib import Path


class ContextExtractor:
    """从 session 目录提取并过滤上下文消息。

    过滤规则：
    - _system_prompt → 保留，role 改为 system
    - _checkpoint → 丢弃
    - _usage → 丢弃
    - user → 保留
    - assistant → 保留并清理（去掉 encrypted 字段）
    - tool → 保留
    """

    # 需要丢弃的 role 类型
    _DROP_ROLES = {"_checkpoint", "_usage"}

    def __init__(self, sessions_dir: Path) -> None:
        """初始化提取器。

        Args:
            sessions_dir: session 根目录，如 ~/.novel/sessions/<hash>
        """
        self.sessions_dir = Path(sessions_dir)

    def extract(self, case_id: str, output_dir: Path) -> bool:
        """提取单个 case 的上下文。

        Args:
            case_id: 用例 ID，如 L1-001
            output_dir: 输出目录，如 eval_results/.../cases/

        Returns:
            是否成功提取。context.jsonl 不存在时返回 False。
        """
        session_id = f"eval-{case_id}"
        context_path = self.sessions_dir / session_id / "context.jsonl"

        if not context_path.exists():
            return False

        # 读取并过滤消息
        messages = self._load_and_filter(context_path)

        # 创建输出子目录
        case_output_dir = output_dir / case_id
        case_output_dir.mkdir(parents=True, exist_ok=True)

        # 写入 messages.jsonl
        self._write_jsonl(messages, case_output_dir / "messages.jsonl")

        # 写入 messages.md
        self._write_markdown(messages, case_output_dir / "messages.md", case_id)

        return True

    def extract_batch(self, case_ids: list[str], output_dir: Path) -> dict[str, bool]:
        """批量提取上下文。

        Args:
            case_ids: 用例 ID 列表
            output_dir: 输出目录

        Returns:
            {case_id: success} 映射，success 表示是否成功提取
        """
        results = {}
        for case_id in case_ids:
            results[case_id] = self.extract(case_id, output_dir)
        return results

    def _load_and_filter(self, context_path: Path) -> list[dict]:
        """读取并过滤 context.jsonl。

        Args:
            context_path: context.jsonl 文件路径

        Returns:
            过滤后的消息列表
        """
        messages = []
        with open(context_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                role = entry.get("role", "")
                if role in self._DROP_ROLES:
                    continue

                # _system_prompt 改为 system
                if role == "_system_prompt":
                    entry["role"] = "system"

                # assistant 去掉 encrypted
                if role == "assistant":
                    content = entry.get("content")
                    if isinstance(content, dict):
                        content = {k: v for k, v in content.items() if k != "encrypted"}
                        entry["content"] = content
                    elif isinstance(content, list):
                        # content 是 list of dicts，移除每个 block 的 encrypted
                        cleaned = []
                        for block in content:
                            if isinstance(block, dict):
                                cleaned.append({k: v for k, v in block.items() if k != "encrypted"})
                            else:
                                cleaned.append(block)
                        entry["content"] = cleaned

                messages.append(entry)
        return messages

    def _write_jsonl(self, messages: list[dict], output_path: Path) -> None:
        """写入 messages.jsonl。

        Args:
            messages: 消息列表
            output_path: 输出文件路径
        """
        with open(output_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def _write_markdown(self, messages: list[dict], output_path: Path, case_id: str) -> None:
        """写入 messages.md。

        Args:
            messages: 消息列表
            output_path: 输出文件路径
            case_id: 用例 ID
        """
        lines = []
        lines.append(f"# {case_id}: Context Messages\n")

        assistant_count = 0
        tool_count = 0

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                lines.append("## [System]\n")
                lines.append("```")
                if isinstance(content, str):
                    lines.append(content)
                lines.append("```\n")

            elif role == "user":
                lines.append("## [User]\n")
                if "(system-reminder)" in content:
                    lines.append("*(system-reminder)*\n")
                lines.append(f"{content}\n")

            elif role == "assistant":
                assistant_count += 1
                lines.append(f"## [Assistant] #{assistant_count}\n")
                # content 可能是 dict 或 list
                if isinstance(content, dict):
                    think = content.get("think", "")
                    if think:
                        lines.append(f"**思考：** {think}\n")
                elif isinstance(content, list) and content:
                    # 处理 list 格式的 content（如多个 content block）
                    for block in content:
                        if isinstance(block, dict):
                            block_type = block.get("type", "")
                            if block_type == "think":
                                think = block.get("think", "")
                                if think:
                                    lines.append(f"**思考：** {think}\n")
                            elif block_type == "text":
                                text = block.get("text", "")
                                if text:
                                    lines.append(f"{text}\n")
                            else:
                                # 兼容其他类型 block
                                think = block.get("think", "")
                                if think:
                                    lines.append(f"**思考：** {think}\n")
                        elif isinstance(block, str):
                            lines.append(f"{block}\n")
                else:
                    if content:
                        lines.append(f"{content}\n")
                # 渲染工具调用参数
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        name = func.get("name", "unknown")
                        args = func.get("arguments", "")
                        lines.append(f"**调用工具：** `{name}`\n")
                        if args:
                            try:
                                parsed = json.loads(args)
                                formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
                                lines.append(f"**参数：**\n```json\n{formatted}\n```\n")
                            except (json.JSONDecodeError, TypeError):
                                lines.append(f"**参数：** `{args}`\n")

            elif role == "tool":
                tool_count += 1
                lines.append(f"## [Tool] #{tool_count}\n")
                if isinstance(content, str):
                    # tool 返回通常是 <system>...</system> 格式
                    lines.append(f"> {content}\n")
                else:
                    lines.append(f"> {json.dumps(content, ensure_ascii=False)}\n")

            lines.append("---\n")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
