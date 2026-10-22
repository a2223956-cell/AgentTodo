"""Content analysis: agent-backed (hermes chat) with a heuristic fallback.

The agent path shells out to the Hermes CLI one-shot mode:
    hermes chat --query-file <q> [--image <img>] -Q --format text --max-turns N
so dropped images/files/text are analyzed by the real agent (it can read
files from disk with its own tools), and the reply is parsed into tasks.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

from .const import MAX_TOTAL_CHARS
from .content import DropContent
from .store import normalize_task

# ---------------------------------------------------------------- JSON parsing

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object containing a tasks/todos array out of a reply."""
    if not text:
        return None
    m = _FENCE_RE.search(text)
    candidates = [m.group(1)] if m else []
    # Balanced-brace scan as a fallback (string-aware).
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1])
                    break
        start = text.find("{", start + 1)
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("tasks", obj.get("todos")), list):
            return obj
    return None


def normalize_tasks(parsed: dict | list | None, source: str = "") -> list[dict]:
    if isinstance(parsed, dict):
        raw_list = parsed.get("tasks", parsed.get("todos", []))
    elif isinstance(parsed, list):
        raw_list = parsed
    else:
        return []
    out = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        if source and not raw.get("source"):
            raw = {**raw, "source": source}
        norm = normalize_task(raw)
        out.append(norm)
        if len(out) >= 20:
            break
    return out


def clean_agent_output(raw: str) -> str:
    """Strip CLI noise (startup warnings, session footer) from --format text output."""
    lines = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s == "cli.startup.warning" or s.startswith("session_id:"):
            continue
        lines.append(ln)
    text = "\n".join(lines)
    cut = text.find("\nsession_id:")
    if cut != -1:
        text = text[:cut]
    return text.strip()


# ------------------------------------------------------------ heuristic parser

_TASK_KEYWORDS = (
    "待办", "todo", "任务", "需要", "必须", "记得", "别忘", "截止", "安排", "会议",
    "提交", "完成", "跟进", "回复", "联系", "购买", "预约", "学习", "准备", "检查",
    "整理", "发送", "写", "改", "修", "确认", "跟进", "deadline", "due", "remind",
)
_DUE_RE = re.compile(
    r"(\d{4}[-/年]\s*\d{1,2}[-/月]\s*\d{1,2}日?"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|(?:今天|明天|后天|大后天|本周[一二三四五六日天]?|下周[一二三四五六日天]?|周[一二三四五六日天]|这周|下[周月]|月底|年前)"
    r"|tomorrow|today|next\s+week)",
    re.I,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*•·]|\d+[.、)]|[（(]?\d+[)）]|\[[ xX]\]|【[^】]{0,8}】)\s*")
_HIGH_KW = ("紧急", "尽快", "asap", "urgent", "马上", "尽快", "重要", "必读")
_LOW_KW = ("不急", "以后", "有空", "someday", "回头", "抽空")


def heuristic_tasks(dc: DropContent) -> list[dict]:
    """Rule-based extraction used when the agent is off/unavailable."""
    out: list[dict] = []
    for label, text in dc.text_blocks:
        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) > 160:
                continue
            stripped = _BULLET_RE.sub("", line).strip()
            if not stripped:
                continue
            is_bullet = bool(_BULLET_RE.match(line))
            has_kw = any(k in stripped.lower() for k in _TASK_KEYWORDS)
            if not (is_bullet or has_kw):
                continue
            m = _DUE_RE.search(stripped)
            due = m.group(0).strip() if m else ""
            low = stripped.lower()
            prio = "high" if any(k in low for k in _HIGH_KW) else (
                "low" if any(k in low for k in _LOW_KW) else "med")
            out.append({
                "title": stripped[:80],
                "detail": "",
                "source": label,
                "due": due,
                "priority": prio,
                "progress": 0,
            })
            if len(out) >= 12:
                break
        if len(out) >= 12:
            break

    if not out:
        # Nothing matched: fall back to the first few meaningful lines,
        # then to one task per unreadable file.
        for label, text in dc.text_blocks:
            for line in text.splitlines():
                line = line.strip()
                if 2 <= len(line) <= 100:
                    out.append({"title": line[:80], "source": label, "progress": 0})
                if len(out) >= 6:
                    break
            if len(out) >= 6:
                break

    for p in dc.image_paths:
        out.append({"title": f"查看图片：{os.path.basename(p)}", "source": "图片", "progress": 0})
    for p in dc.other_paths:
        out.append({"title": f"处理文件：{os.path.basename(p)}", "source": "文件", "progress": 0})
    return out[:20]


# ----------------------------------------------------------------- agent path

def find_hermes_bin() -> str | None:
    exe = shutil.which("hermes") or shutil.which("hermes.cmd")
    if exe:
        return exe
    home = os.environ.get("HERMES_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "hermes")
    for cand in (os.path.join(home, "bin", "hermes.cmd"),
                 os.path.join(home, "bin", "hermes")):
        if os.path.exists(cand):
            return cand
    return None


PROMPT_HEADER = """你是任务提取助手。请阅读下面的内容(可能包含文本、图片路径和文件路径),提炼出需要跟进的待办任务。
只输出一个 JSON 对象,不要输出任何解释或其他文字。

输出格式:
{"tasks":[{"title":"任务标题","detail":"补充说明","due":"截止时间,没有则为空字符串","priority":"high|med|low","mode":"check|pct","customer":"客户名称,没有则空","order_date":"订单日期,没有则空","amount":"金额(含币种),没有则空","progress":0}]}

规则:
1. title 用简体中文、简洁明确、动词开头,不超过 30 个字。
2. 图片和文档请用你的读取工具打开对应路径后再提取任务。
3. 提取所有明确的待办/日程/工作安排;内容里没有明确任务时,提取最值得跟进的 1-5 条事项。
4. due 只保留原文出现的时间描述;progress 一律填 0。
5. mode 只能是 check 或 pct:目标是可量化推进的(如"拜访 30 家客户""销量做到 50 万""整理 100 条数据""完成率 80%")用 pct;
   一次做完就结束的事项(如"回复邮件""提交报价单""预约牙医")用 check。判断不了时用 check。
6. 订单/合同/PO/PI/形式发票类单据:第一个任务是"跟进 <客户名> 的 <单号> 交付/处理",标题里必须写出客户名称(有单号就带单号);
   customer 填客户或收货方名称,order_date 填单据上的订单日期(原样照抄),amount 填订单总金额(保留币种与千分位,例如 "USD 12,345.00");
   due 优先取交期/发货日期。单据里找不到的字段一律填空字符串,不要猜。
7. 同一张订单可以再拆出 1-2 条具体执行任务(核对产品清单与数量、联系客户确认交期等),这些任务同样复用上面的 customer / order_date / amount。
"""


def build_query(dc: DropContent, extra_text: str = "") -> str:
    parts = [PROMPT_HEADER]
    text = dc.text_for_prompt()
    if extra_text:
        text = (text + "\n\n【补充】\n" + extra_text).strip()
    if text:
        parts.append("--- 文本内容 ---\n" + text[:MAX_TOTAL_CHARS])
    refs = []
    for p in dc.image_paths:
        refs.append(f"[图片] {p}")
    for p in dc.other_paths:
        refs.append(f"[文件] {p}")
    if refs:
        parts.append("--- 需要读取的文件路径 ---\n" + "\n".join(refs))
    return "\n\n".join(parts)


def _cmd_quote(arg: str) -> str:
    """Quote one argument for cmd.exe so metacharacters (& | > ^) stay literal."""
    return '"' + arg.replace('"', '""') + '"'


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a process and its children (.cmd shims leave node grandkids behind)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


class AgentAnalyzer:
    """Runs `hermes chat` one-shot and parses tasks out of the reply."""

    def __init__(self, timeout: int = 200, max_turns: int = 6,
                 run_budget: int = 170, exe: str | None = None):
        self.timeout = timeout
        self.max_turns = max_turns
        self.run_budget = run_budget
        self.exe = exe or find_hermes_bin()

    def available(self) -> bool:
        return bool(self.exe)

    def analyze(self, dc: DropContent, extra_text: str = "") -> tuple[list[dict], str]:
        if not self.exe:
            return [], "agent 不可用"
        query = build_query(dc, extra_text)
        fd, qpath = tempfile.mkstemp(suffix=".txt", prefix="agenttodo_", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(query)
            args = self._cmd(qpath, dc)
            proc = self._run(args)
            if proc is None:
                return [], "agent 调用失败"
            out = clean_agent_output(proc.stdout or "")
            parsed = extract_json(out)
            tasks = normalize_tasks(parsed, source=dc.label)
            if not tasks:
                return [], "agent 未能返回任务 JSON"
            return tasks, f"agent 已分析 {len(tasks)} 条任务"
        except subprocess.TimeoutExpired:
            return [], "agent 分析超时"
        except OSError as e:
            return [], f"agent 调用失败: {e}"
        finally:
            try:
                os.unlink(qpath)
            except OSError:
                pass

    def _cmd(self, qpath: str, dc: DropContent) -> list[str]:
        exe = self.exe or ""
        args = [exe, "chat", "--query-file", qpath, "-Q", "--format", "text",
                "--max-turns", str(self.max_turns),
                "--run-budget", str(self.run_budget)]
        if dc.image_paths:
            args += ["--image", dc.image_paths[0]]
        return args

    def _run(self, args: list[str]) -> subprocess.CompletedProcess | None:
        """Execute hermes; batch shims need cmd.exe parsing, so route accordingly.

        Batch targets (.bat/.cmd) always go through a fully quoted cmd.exe
        command line: CreateProcess hands their arguments to cmd for
        re-parsing, which splits unquoted args at & | > ^ (a dropped file
        named "a&b.png" would become two commands). Timeouts kill the whole
        process tree and propagate as TimeoutExpired.
        """
        exe = str(args[0] or "").lower()
        if not (exe.endswith(".bat") or exe.endswith(".cmd")):
            try:
                return self._exec(args)
            except (OSError, ValueError):
                pass
        if os.name != "nt":
            return None
        try:
            return self._exec_shell(" ".join(_cmd_quote(a) for a in args))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _spawn_kwargs() -> dict:
        """Launch hermes with piped stdio and NO console window.

        The app runs under pythonw.exe, which owns no console; Windows would
        therefore allocate a brand-new visible console for the console-subsystem
        hermes child. That window is not just noise: it hands the CLI a real TTY
        on stdin, so it takes the interactive/approval path and blocks until the
        analyzer's timeout fires — the drop then silently degrades to the
        heuristic parser. Piped stdio + CREATE_NO_WINDOW keeps the CLI on its
        deterministic one-shot path.
        """
        kw: dict = {"stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW
        return kw

    def _exec(self, args: list[str]) -> subprocess.CompletedProcess:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", shell=False,
            **self._spawn_kwargs(),
        )
        return self._communicate(proc, args)

    def _exec_shell(self, cmdline: str) -> subprocess.CompletedProcess:
        """Run a pre-quoted command line through cmd.exe (shell=True)."""
        proc = subprocess.Popen(
            cmdline, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", shell=True,
            **self._spawn_kwargs(),
        )
        return self._communicate(proc, [cmdline])

    def _communicate(self, proc: subprocess.Popen,
                     args: list[str]) -> subprocess.CompletedProcess:
        try:
            out, err = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            raise
        return subprocess.CompletedProcess(args, proc.returncode, out, err)


class Analyzer:
    """Facade: agent-first (when enabled and available), heuristic fallback."""

    def __init__(self, agent_enabled: bool = True, agent: AgentAnalyzer | None = None):
        self.agent = agent or AgentAnalyzer()
        self.agent_enabled = agent_enabled

    def analyze(self, dc: DropContent, extra_text: str = "") -> tuple[list[dict], str]:
        used_agent = False
        if self.agent_enabled and self.agent.available():
            tasks, note = self.agent.analyze(dc, extra_text)
            if not tasks:
                # One retry: a transient CLI/API blip must not silently degrade
                # a drop into the rule parser (which yields "处理文件：<name>").
                retry_tasks, retry_note = self.agent.analyze(dc, extra_text)
                if retry_tasks:
                    tasks, note = retry_tasks, retry_note + "(重试成功)"
                else:
                    note = f"{note} · 重试后仍失败"
            used_agent = bool(tasks)
        else:
            tasks, note = [], ("AI 分析已关闭" if not self.agent_enabled
                               else "agent 不可用")
        if not tasks:
            tasks = heuristic_tasks(dc)
            if extra_text.strip() and not any(
                    t.get("source") == "输入文字" for t in tasks):
                tasks = [{"title": extra_text.strip()[:80], "source": "输入文字",
                          "progress": 0}] + tasks
            note = (note + " · 已用规则解析" if note else "已用规则解析")
        return tasks, note
