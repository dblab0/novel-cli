"""媒体文件读取工具模块。

提供读取图片和视频文件的能力，支持将媒体内容转换为 LLM 可理解的格式。
"""

import base64
from io import BytesIO
from pathlib import Path
from typing import override

from kaos.path import KaosPath
from kosong.chat_provider.kimi import Kimi
from kosong.tooling import CallableTool2, ToolError, ToolOk, ToolReturnValue
from pydantic import BaseModel, Field

from novel_cli.soul.agent import Runtime
from novel_cli.tools import SkipThisTool
from novel_cli.tools.file.utils import MEDIA_SNIFF_BYTES, FileType, detect_file_type
from novel_cli.tools.utils import load_desc
from novel_cli.utils.media_tags import wrap_media_part
from novel_cli.utils.path import is_within_workspace
from novel_cli.wire.types import ImageURLPart, VideoURLPart

MAX_MEDIA_MEGABYTES = 100


def _to_data_url(mime_type: str, data: bytes) -> str:
    """将媒体数据转换为 data URL 格式。

    Args:
        mime_type: MIME 类型字符串。
        data: 媒体数据字节。

    Returns:
        Base64 编码的 data URL 字符串。
    """
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _extract_image_size(data: bytes) -> tuple[int, int] | None:
    """提取图片的尺寸信息。

    Args:
        data: 图片数据字节。

    Returns:
        图片尺寸元组 (宽度, 高度)，如果无法提取则返回 None。
    """
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            return image.size
    except Exception:
        return None


class Params(BaseModel):
    """ReadMediaFile 工具的参数模型。

    Attributes:
        path: 要读取的文件路径。读取工作目录外的文件时需要使用绝对路径。
    """

    path: str = Field(
        description=(
            "The path to the file to read. Absolute paths are required when reading files "
            "outside the working directory."
        )
    )


class ReadMediaFile(CallableTool2[Params]):
    """媒体文件读取工具类。

    用于读取图片和视频文件，将其转换为 LLM 可接受的格式。

    Attributes:
        name: 工具名称。
        params: 参数类型。
    """

    name: str = "ReadMediaFile"
    params: type[Params] = Params

    def __init__(self, runtime: Runtime) -> None:
        """初始化 ReadMediaFile 工具实例。

        Args:
            runtime: 运行时环境对象。

        Raises:
            SkipThisTool: 当 LLM 不支持图片或视频输入能力时抛出。
        """
        capabilities = runtime.llm.capabilities if runtime.llm else set[str]()
        if "image_in" not in capabilities and "video_in" not in capabilities:
            raise SkipThisTool()

        description = load_desc(
            Path(__file__).parent / "read_media.md",
            {
                "MAX_MEDIA_MEGABYTES": MAX_MEDIA_MEGABYTES,
                "capabilities": capabilities,
            },
        )
        super().__init__(description=description)

        self._runtime = runtime
        self._work_dir = runtime.builtin_args.NOVEL_WORK_DIR
        self._additional_dirs = runtime.additional_dirs
        self._capabilities = capabilities

    async def _validate_path(self, path: KaosPath) -> ToolError | None:
        """验证路径是否可以安全读取。

        Args:
            path: 要验证的路径对象。

        Returns:
            如果路径无效则返回 ToolError，否则返回 None。
        """
        resolved_path = path.canonical()

        if (
            not is_within_workspace(resolved_path, self._work_dir, self._additional_dirs)
            and not path.is_absolute()
        ):
            # 工作目录外的文件只能通过绝对路径读取
            return ToolError(
                message=(
                    f"`{path}` is not an absolute path. "
                    "You must provide an absolute path to read a file "
                    "outside the working directory."
                ),
                brief="Invalid path",
            )
        return None

    async def _read_media(self, path: KaosPath, file_type: FileType) -> ToolReturnValue:
        """读取媒体文件并转换为 LLM 可接受的格式。

        Args:
            path: 文件路径对象。
            file_type: 文件类型信息。

        Returns:
            工具执行结果，包含媒体数据或错误信息。
        """
        assert file_type.kind in ("image", "video")

        media_path = str(path)
        stat = await path.stat()
        size = stat.st_size
        if size == 0:
            return ToolError(
                message=f"`{path}` is empty.",
                brief="Empty file",
            )
        if size > (MAX_MEDIA_MEGABYTES << 20):
            return ToolError(
                message=(
                    f"`{path}` is {size} bytes, which exceeds the max "
                    f"{MAX_MEDIA_MEGABYTES}MB bytes for media files."
                ),
                brief="File too large",
            )

        match file_type.kind:
            case "image":
                data = await path.read_bytes()
                data_url = _to_data_url(file_type.mime_type, data)
                part = ImageURLPart(image_url=ImageURLPart.ImageURL(url=data_url))
                wrapped = wrap_media_part(part, tag="image", attrs={"path": media_path})
                image_size = _extract_image_size(data)
            case "video":
                data = await path.read_bytes()
                if (llm := self._runtime.llm) and isinstance(llm.chat_provider, Kimi):
                    part = await llm.chat_provider.files.upload_video(
                        data=data,
                        mime_type=file_type.mime_type,
                    )
                    wrapped = wrap_media_part(part, tag="video", attrs={"path": media_path})
                else:
                    data_url = _to_data_url(file_type.mime_type, data)
                    part = VideoURLPart(video_url=VideoURLPart.VideoURL(url=data_url))
                    wrapped = wrap_media_part(part, tag="video", attrs={"path": media_path})
                image_size = None

        size_hint = ""
        if image_size:
            size_hint = f", original size {image_size[0]}x{image_size[1]}px"
        note = (
            " If you need to output coordinates, output relative coordinates first and "
            "compute absolute coordinates using the original image size; if you generate or "
            "edit images/videos via commands or scripts, read the result back immediately "
            "before continuing."
        )
        return ToolOk(
            output=wrapped,
            message=(
                f"Loaded {file_type.kind} file `{path}` "
                f"({file_type.mime_type}, {size} bytes{size_hint}).{note}"
            ),
        )

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        """执行媒体文件读取操作。

        Args:
            params: 读取参数。

        Returns:
            工具执行结果，包含媒体内容或错误信息。
        """
        if not params.path:
            return ToolError(
                message="File path cannot be empty.",
                brief="Empty file path",
            )

        try:
            p = KaosPath(params.path).expanduser()
            if err := await self._validate_path(p):
                return err
            p = p.canonical()

            if not await p.exists():
                return ToolError(
                    message=f"`{params.path}` does not exist.",
                    brief="File not found",
                )
            if not await p.is_file():
                return ToolError(
                    message=f"`{params.path}` is not a file.",
                    brief="Invalid path",
                )

            header = await p.read_bytes(MEDIA_SNIFF_BYTES)
            file_type = detect_file_type(str(p), header=header)
            if file_type.kind == "text":
                return ToolError(
                    message=f"`{params.path}` is a text file. Use ReadFile to read text files.",
                    brief="Unsupported file type",
                )
            if file_type.kind == "unknown":
                return ToolError(
                    message=(
                        f"`{params.path}` seems not readable as an image or video file. "
                        "You may need to read it with proper shell commands, Python tools "
                        "or MCP tools if available. "
                        "If you read/operate it with Python, you MUST ensure that any "
                        "third-party packages are installed in a virtual environment (venv)."
                    ),
                    brief="File not readable",
                )

            if file_type.kind == "image" and "image_in" not in self._capabilities:
                return ToolError(
                    message=(
                        "The current model does not support image input. "
                        "Tell the user to use a model with image input capability."
                    ),
                    brief="Unsupported media type",
                )
            if file_type.kind == "video" and "video_in" not in self._capabilities:
                return ToolError(
                    message=(
                        "The current model does not support video input. "
                        "Tell the user to use a model with video input capability."
                    ),
                    brief="Unsupported media type",
                )

            return await self._read_media(p, file_type)
        except Exception as e:
            return ToolError(
                message=f"Failed to read {params.path}. Error: {e}",
                brief="Failed to read file",
            )
