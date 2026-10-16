"""App-wide constants and paths for AgentTodo floating widget."""
import os

APP_NAME = "AgentTodo"
APP_NAME_CN = "AgentTodo 悬浮待办"
VERSION = "1.0.0"

# Text pulled from dropped files is capped so agent prompts stay bounded.
MAX_FILE_CHARS = 6000
MAX_TOTAL_CHARS = 9000

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
DOCX_EXT = {".docx"}
# Formats we never attempt to decode as text — the agent reads them by path.
BINARY_EXT = {".pdf", ".doc", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".7z",
              ".rar", ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".exe", ".dll",
              ".db", ".sqlite", ".pyc", ".iso", ".psd"}
# Anything else small enough is attempted as plain text.


def data_dir(override: str | None = None) -> str:
    if override:
        d = override
    else:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local"
        )
        d = os.path.join(base, "AgentTodoFloat")
    os.makedirs(d, exist_ok=True)
    return d


def store_path(d: str) -> str:
    return os.path.join(d, "todos.json")


def config_path(d: str) -> str:
    return os.path.join(d, "config.json")


def tmp_dir(d: str) -> str:
    t = os.path.join(d, "tmp")
    os.makedirs(t, exist_ok=True)
    return t
