"""入口：启动 FastAPI 服务。"""

import uvicorn


def main() -> None:
    uvicorn.run("novel_debug.app:app", host="0.0.0.0", port=9004, reload=False)


if __name__ == "__main__":
    main()
