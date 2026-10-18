"""AgentTodo — 基于 agent 的桌面悬浮待办插件.

用法:
    python main.py                启动悬浮球
    python main.py --smoke        自检模式(自动演示并退出)
    python main.py --no-agent     本次关闭 AI 分析(使用规则解析)
    python main.py --reset-position   重置悬浮球位置后启动
    python main.py --install-startup  添加开机自启
    python main.py --uninstall-startup  移除开机自启
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import const, lock  # noqa: E402

STARTUP_NAME = "AgentTodo.bat"


def startup_path() -> str:
    appdata = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", STARTUP_NAME)


def install_startup() -> None:
    appdir = os.path.dirname(os.path.abspath(__file__))
    bat = os.path.join(appdir, "run.bat")
    with open(startup_path(), "w", encoding="utf-8") as f:
        f.write(f'@echo off\r\nstart "" "{bat}"\r\n')
    print(f"已添加开机自启: {startup_path()}")


def uninstall_startup() -> None:
    p = startup_path()
    if os.path.exists(p):
        os.remove(p)
        print(f"已移除开机自启: {p}")
    else:
        print("未找到开机自启项,无需移除")


def main() -> int:
    args = sys.argv[1:]
    smoke = "--smoke" in args
    agent_enabled: bool | None = False if "--no-agent" in args else None
    data_dir = None
    screenshot = None

    if "--install-startup" in args:
        install_startup()
        return 0
    if "--uninstall-startup" in args:
        uninstall_startup()
        return 0
    if "--data-dir" in args:
        data_dir = args[args.index("--data-dir") + 1]
    if "--screenshot" in args:
        screenshot = args[args.index("--screenshot") + 1]
        os.environ["AGENTTODO_SMOKE_SHOT"] = screenshot

    if not lock.acquire(const.data_dir(data_dir)):
        lock.notify_duplicate()
        return 1

    from app.app import App

    app = App(data_dir=data_dir, agent_enabled=agent_enabled, smoke=smoke)
    if "--reset-position" in args:
        app.reset_position()
    if smoke and "--smoke-agent" not in args:
        app.set_agent_enabled(False)  # deterministic smoke unless agent requested
    app.run()
    lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
