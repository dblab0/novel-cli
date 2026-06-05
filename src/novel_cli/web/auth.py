"""Novel CLI Web 认证辅助模块和中间件。

提供 Bearer Token 认证、来源检查和局域网访问限制等功能。
"""

from __future__ import annotations

import hmac
import ipaddress
import re
from collections.abc import Iterable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

DEFAULT_ALLOWED_ORIGIN_REGEX = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")


def timing_safe_compare(a: str, b: str) -> bool:
    """时序安全的字符串比较。

    使用恒定时间比较防止时序攻击。

    Args:
        a: 第一个字符串。
        b: 第二个字符串。

    Returns:
        两个字符串是否相等。
    """
    return hmac.compare_digest(a.encode(), b.encode())


def parse_bearer_token(value: str | None) -> str | None:
    """从 Authorization 头提取 Bearer Token。

    Args:
        value: Authorization 头的值。

    Returns:
        提取的 Token，格式不正确时返回 None。
    """
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def normalize_allowed_origins(value: str | None) -> list[str]:
    """将逗号分隔的来源字符串解析为规范化列表。

    Args:
        value: 逗号分隔的来源字符串。

    Returns:
        规范化后的来源列表。
    """
    if not value:
        return []
    origins: list[str] = []
    for raw in value.split(","):
        origin = raw.strip().rstrip("/")
        if origin:
            origins.append(origin)
    return origins


def is_origin_allowed(origin: str, allowed_origins: Iterable[str] | None) -> bool:
    """检查来源是否被允许。

    Args:
        origin: 要检查的来源。
        allowed_origins: 允许的来源列表。
            - None: 使用默认的 localhost 正则匹配。
            - 空列表: 拒绝所有来源。
            - 非空列表: 检查列表（支持 "*" 通配符）。

    Returns:
        来源是否被允许。
    """
    origin = origin.rstrip("/")

    # None 表示使用默认行为（仅 localhost）
    if allowed_origins is None:
        return bool(DEFAULT_ALLOWED_ORIGIN_REGEX.match(origin))

    allowed = list(allowed_origins)

    # 空列表明确表示拒绝所有
    if not allowed:
        return False

    # 检查通配符或精确匹配
    if "*" in allowed:
        return True
    return origin in allowed


def extract_token_from_request(request: Request) -> str | None:
    """从请求中提取认证 Token。

    支持 Authorization 头或 GET 请求的查询参数。

    Args:
        request: FastAPI 请求对象。

    Returns:
        提取的 Token，未找到时返回 None。
    """
    token = parse_bearer_token(request.headers.get("authorization"))
    if token:
        return token
    if request.method.upper() == "GET":
        query_token = request.query_params.get("token")
        if query_token:
            return query_token
    return None


def verify_token(provided: str | None, expected: str) -> bool:
    """使用时序安全比较验证 Token。

    Args:
        provided: 提供的 Token。
        expected: 预期的 Token。

    Returns:
        Token 是否匹配。
    """
    if not provided:
        return False
    return timing_safe_compare(provided, expected)


def is_private_ip(ip: str) -> bool:
    """检查 IP 地址是否属于私有地址范围（RFC 1918 + localhost）。

    支持 IPv4 和 IPv6 地址。

    Args:
        ip: IP 地址字符串。

    Returns:
        是否为私有地址。
    """
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
        # is_private 包含 RFC 1918 (10.x, 172.16-31.x, 192.168.x)
        # is_loopback 包含 127.x.x.x 和 ::1
        # is_link_local 包含 169.254.x.x 和 fe80::/10
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        return False


def get_client_ip(request: Request, trust_proxy: bool = False) -> str | None:
    """从请求中提取客户端 IP 地址。

    Args:
        request: 传入的请求对象。
        trust_proxy: 是否信任 X-Forwarded-For 头（仅在可信代理后启用）。

    Returns:
        客户端 IP 地址，无法获取时返回 None。
    """
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件，提供 Token 认证、来源检查和局域网访问限制。

    用于保护 API 路由的安全。

    Attributes:
        _session_token: 会话 Token，用于认证。
        _allowed_origins: 允许的来源列表。
        _enforce_origin: 是否强制来源检查。
        _lan_only: 是否仅允许局域网访问。
    """

    def __init__(
        self,
        app: ASGIApp,
        session_token: str | None,
        allowed_origins: Iterable[str] | None,
        enforce_origin: bool,
        lan_only: bool = False,
    ) -> None:
        """初始化认证中间件。

        Args:
            app: ASGI 应用实例。
            session_token: 会话 Token。
            allowed_origins: 允许的来源列表。
            enforce_origin: 是否强制来源检查。
            lan_only: 是否仅允许局域网访问。
        """
        super().__init__(app)
        self._session_token = session_token
        self._allowed_origins = list(allowed_origins) if allowed_origins is not None else None
        self._enforce_origin = enforce_origin
        self._lan_only = lan_only

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        """处理请求并执行认证检查。

        Args:
            request: 请求对象。
            call_next: 下一个中间件或路由处理器。

        Returns:
            响应对象。
        """
        path = request.url.path

        # 局域网检查应用于所有请求（包括静态文件）
        if self._lan_only:
            client_ip = get_client_ip(request)
            if client_ip and not is_private_ip(client_ip):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Access denied: only local network access is allowed"},
                )

        if request.method.upper() == "OPTIONS":
            return await call_next(request)
        if path in {"/healthz", "/docs", "/scalar"}:
            return await call_next(request)
        if not path.startswith("/api/"):
            return await call_next(request)

        if self._enforce_origin:
            origin = request.headers.get("origin")
            if origin and not is_origin_allowed(origin, self._allowed_origins):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Origin not allowed"},
                )

        if self._session_token:
            provided = extract_token_from_request(request)
            if not verify_token(provided, self._session_token):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized"},
                )

        return await call_next(request)


__all__ = [
    "AuthMiddleware",
    "DEFAULT_ALLOWED_ORIGIN_REGEX",
    "extract_token_from_request",
    "get_client_ip",
    "is_origin_allowed",
    "is_private_ip",
    "normalize_allowed_origins",
    "timing_safe_compare",
    "verify_token",
]
