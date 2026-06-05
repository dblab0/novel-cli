from __future__ import annotations


class NovelCLIException(Exception):
    """Base exception class for Novel CLI."""

    pass


class ConfigError(NovelCLIException, ValueError):
    """Configuration error."""

    pass


class AgentSpecError(NovelCLIException, ValueError):
    """Agent specification error."""

    pass


class InvalidToolError(NovelCLIException, ValueError):
    """Invalid tool error."""

    pass


class SystemPromptTemplateError(NovelCLIException, ValueError):
    """System prompt template error."""

    pass


class MCPConfigError(NovelCLIException, ValueError):
    """MCP config error."""

    pass


class MCPRuntimeError(NovelCLIException, RuntimeError):
    """MCP runtime error."""

    pass
