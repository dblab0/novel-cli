"""服务器启动共享工具模块。

提供 novel vis 和 novel web 服务器启动所需的网络地址处理和端口查找功能。
"""

from __future__ import annotations

import importlib
import socket
import textwrap


def get_address_family(host: str) -> socket.AddressFamily:
    """根据主机地址返回地址族。

    Args:
        host: 主机地址字符串。

    Returns:
        如果地址包含冒号则返回 AF_INET6（IPv6），否则返回 AF_INET（IPv4）。
    """
    return socket.AF_INET6 if ":" in host else socket.AF_INET


def format_url(host: str, port: int) -> str:
    """构建 HTTP URL 字符串。

    按照 RFC 2732 规范对 IPv6 地址添加方括号。

    Args:
        host: 主机地址。
        port: 端口号。

    Returns:
        格式化的 URL 字符串，如 "http://host:port" 或 "http://[::1]:port"。
    """
    if ":" in host:
        return f"http://[{host}]:{port}"
    return f"http://{host}:{port}"


def is_local_host(host: str) -> bool:
    """检查主机是否解析为本地回环地址。

    Args:
        host: 主机地址字符串。

    Returns:
        如果是本地回环地址则返回 True，否则返回 False。
    """
    return host in {"127.0.0.1", "localhost", "::1"}


def find_available_port(host: str, start_port: int, max_attempts: int = 10) -> int:
    """查找可用的端口号。

    从 start_port 开始尝试绑定，直到找到可用端口或达到最大尝试次数。

    Args:
        host: 要绑定的主机地址。
        start_port: 起始端口号。
        max_attempts: 最大尝试次数，默认为 10。

    Returns:
        找到的可用端口号。

    Raises:
        ValueError: 如果 max_attempts 不是正数或 start_port 超出有效范围。
        RuntimeError: 如果在指定范围内找不到可用端口。
    """
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    if start_port < 1 or start_port > 65535:
        raise ValueError("start_port must be between 1 and 65535")

    family = get_address_family(host)
    for offset in range(max_attempts):
        port = start_port + offset
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"Cannot find available port in range {start_port}-{start_port + max_attempts - 1}"
    )


def get_network_addresses() -> list[str]:
    """获取本机的非回环 IPv4 地址列表。

    通过多种方式尝试获取网络地址：
    1. 通过主机名解析
    2. 通过 UDP 连接获取出口地址
    3. 通过 netifaces 库（如果可用）

    Returns:
        非回环 IPv4 地址字符串列表。
    """
    addresses: list[str] = []

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if isinstance(ip, str) and not ip.startswith("127.") and ip not in addresses:
                addresses.append(ip)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        if ip and not ip.startswith("127.") and ip not in addresses:
            addresses.append(ip)
    except OSError:
        pass

    try:
        netifaces = importlib.import_module("netifaces")
        for interface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(interface)
            if netifaces.AF_INET in addrs:
                for addr_info in addrs[netifaces.AF_INET]:
                    addr = addr_info.get("addr")
                    if addr and not addr.startswith("127.") and addr not in addresses:
                        addresses.append(addr)
    except (ImportError, Exception):
        pass

    return addresses


def print_banner(lines: list[str]) -> None:
    """打印带边框的横幅文本。

    支持特殊标签约定：<center> 居中对齐、<nowrap> 不换行、<hr> 水平分隔线。

    Args:
        lines: 要打印的文本行列表。
    """
    processed: list[str] = []
    for line in lines:
        if line == "<hr>":
            processed.append(line)
        elif not line:
            processed.append("")
        elif line.startswith("<center>") or line.startswith("<nowrap>"):
            processed.append(line)
        else:
            processed.extend(textwrap.wrap(line, width=78))

    def strip_tags(s: str) -> str:
        return s.removeprefix("<center>").removeprefix("<nowrap>")

    content_lines = [strip_tags(line) for line in processed if line != "<hr>"]
    width = max(60, *(len(line) for line in content_lines))
    top = "+" + "=" * (width + 2) + "+"

    print(top)
    for line in processed:
        if line == "<hr>":
            print("|" + "-" * (width + 2) + "|")
        elif line.startswith("<center>"):
            content = line.removeprefix("<center>")
            print(f"| {content.center(width)} |")
        elif line.startswith("<nowrap>"):
            content = line.removeprefix("<nowrap>")
            print(f"| {content.ljust(width)} |")
        else:
            print(f"| {line.ljust(width)} |")
    print(top)