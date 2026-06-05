"""ACP 内容转换模块。

本模块提供 ACP 内容块与内部内容组件之间的双向转换功能，用于消息处理和工具结果转换。
"""

from __future__ import annotations

import acp

from novel_cli.acp.types import ACPContentBlock
from novel_cli.utils.logging import logger
from novel_cli.wire.types import (
    ContentPart,
    DiffDisplayBlock,
    DisplayBlock,
    ImageURLPart,
    TextPart,
    ToolReturnValue,
)


def acp_blocks_to_content_parts(prompt: list[ACPContentBlock]) -> list[ContentPart]:
    """将 ACP 内容块列表转换为内部内容组件列表。

    Args:
        prompt: ACP 内容块列表。

    Returns:
        内部内容组件列表。
    """
    content: list[ContentPart] = []
    for block in prompt:
        match block:
            case acp.schema.TextContentBlock():
                content.append(TextPart(text=block.text))
            case acp.schema.ImageContentBlock():
                content.append(
                    ImageURLPart(
                        image_url=ImageURLPart.ImageURL(
                            url=f"data:{block.mime_type};base64,{block.data}"
                        )
                    )
                )
            case acp.schema.EmbeddedResourceContentBlock():
                resource = block.resource
                if isinstance(resource, acp.schema.TextResourceContents):
                    uri = resource.uri
                    text = resource.text
                    content.append(TextPart(text=f"<resource uri={uri!r}>\n{text}\n</resource>"))
                else:
                    logger.warning(
                        "Unsupported embedded resource type: {type}",
                        type=type(resource).__name__,
                    )
            case acp.schema.ResourceContentBlock():
                # ResourceContentBlock 是不带内联内容的链接引用；
                # 包含 URI 以便模型至少知道该引用的存在
                content.append(
                    TextPart(text=f"<resource_link uri={block.uri!r} name={block.name!r} />")
                )
            case _:
                logger.warning("Unsupported prompt content block: {block}", block=block)
    return content


def display_block_to_acp_content(
    block: DisplayBlock,
) -> acp.schema.FileEditToolCallContent | None:
    """将显示块转换为 ACP 工具调用内容。

    Args:
        block: 显示块对象。

    Returns:
        ACP 文件编辑工具调用内容，如果不支持则返回 None。
    """
    if isinstance(block, DiffDisplayBlock):
        return acp.schema.FileEditToolCallContent(
            type="diff",
            path=block.path,
            old_text=block.old_text,
            new_text=block.new_text,
        )

    return None


def tool_result_to_acp_content(
    tool_ret: ToolReturnValue,
) -> list[
    acp.schema.ContentToolCallContent
    | acp.schema.FileEditToolCallContent
    | acp.schema.TerminalToolCallContent
]:
    """将工具结果转换为 ACP 工具调用内容列表。

    Args:
        tool_ret: 工具返回值对象。

    Returns:
        ACP 工具调用内容列表，如果需要隐藏输出则返回空列表。
    """
    from novel_cli.acp.tools import HideOutputDisplayBlock

    def _to_acp_content(
        part: ContentPart,
    ) -> (
        acp.schema.ContentToolCallContent
        | acp.schema.FileEditToolCallContent
        | acp.schema.TerminalToolCallContent
    ):
        """将内容组件转换为 ACP 工具调用内容。

        Args:
            part: 内容组件对象。

        Returns:
            ACP 工具调用内容对象。
        """
        if isinstance(part, TextPart):
            return acp.schema.ContentToolCallContent(
                type="content", content=acp.schema.TextContentBlock(type="text", text=part.text)
            )
        logger.warning("Unsupported content part in tool result: {part}", part=part)
        return acp.schema.ContentToolCallContent(
            type="content",
            content=acp.schema.TextContentBlock(type="text", text=f"[{part.__class__.__name__}]"),
        )

    def _to_text_block(text: str) -> acp.schema.ContentToolCallContent:
        """将文本转换为 ACP 内容工具调用内容。

        Args:
            text: 文本内容。

        Returns:
            ACP 内容工具调用内容对象。
        """
        return acp.schema.ContentToolCallContent(
            type="content", content=acp.schema.TextContentBlock(type="text", text=text)
        )

    contents: list[
        acp.schema.ContentToolCallContent
        | acp.schema.FileEditToolCallContent
        | acp.schema.TerminalToolCallContent
    ] = []

    for block in tool_ret.display:
        if isinstance(block, HideOutputDisplayBlock):
            # 提前返回以表示不应显示输出
            return []

        content = display_block_to_acp_content(block)
        if content is not None:
            contents.append(content)
    # TODO: 更好的 display 块和 output 拼接方式？

    output = tool_ret.output
    if isinstance(output, str):
        if output:
            contents.append(_to_text_block(output))
    else:
        # 注意：目前 ToolReturnValue.output 要么是字符串要么是 ContentPart 列表。
        # 我们避免不必要的 isinstance() 检查，以保持 pyright 满意，同时处理列表输出。
        contents.extend(_to_acp_content(part) for part in output)

    if not contents and tool_ret.message:
        # 如果没有其他内容，回退到 LLM 的 message
        contents.append(_to_text_block(tool_ret.message))

    return contents