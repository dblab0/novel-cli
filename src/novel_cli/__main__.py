from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path


def _prog_name() -> str:
    return Path(sys.argv[0]).name or "novel"


def main(argv: Sequence[str] | None = None) -> int | str | None:
    from novel_cli.utils.proxy import normalize_proxy_env

    normalize_proxy_env()

    args = list(sys.argv[1:] if argv is None else argv)

    if len(args) == 1 and args[0] in {"--version", "-V"}:
        from novel_cli.constant import get_version

        print(f"novel, version {get_version()}")
        return 0

    from novel_cli.cli import cli

    try:
        return cli(args=args, prog_name=_prog_name())
    except SystemExit as exc:
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
