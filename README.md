# AgentTodo — 基于 AI Agent 的桌面悬浮待办

> 把图片、文件或文字丢给屏幕角落的悬浮球,AI 帮你把"要做的事"变成一张带进度的待办清单。

AgentTodo 是一个 Windows 桌面悬浮窗小工具:拖一张截图、一份文档或一段文字进面板,**Hermes agent** 自动分析内容、提取待办任务并跟踪进度。整个交互收敛在一个可拖动的小圆球上——不用打开任何软件,待办就在桌面上。

## 为什么做这个

日常待办往往散落在截图、聊天记录、文档和一闪而过的念头里。整理它们本身就是一件"待办",于是常常被拖着不做。AgentTodo 的思路:**不整理,直接扔**——内容丢给悬浮球,AI 负责提炼成任务,你只负责勾选完成。

## 特性亮点

- **桌面悬浮球**:置顶小圆球,实时显示总进度环(百分比 + 完成数/总数),可拖到屏幕任意位置。
- **悬停即用**:鼠标悬停展开任务 dashboard,移开自动收起;双击或点「◎」可固定面板。
- **拖入即分析**:图片(AI 直接"看图")、.txt / .md / .csv / .json / 代码(自动识别 GBK/UTF-8/UTF-16)、.docx(抽取正文)、pdf / xls 等二进制文件都能丢进去。
- **两种添加方式**:「AI 分析」/ 回车走 agent 提取任务(含标题、说明、截止时间、优先级);「+」按行快速手动添加,不走 AI。
- **进度跟踪**:勾选完成、点击进度条 +25%(右键 -25%)、删除;悬浮球与面板顶部实时汇总。
- **AI 可降级**:通过 `hermes chat` 复用你已配置的模型;AI 不可用或手动关闭时自动退回内置规则解析(离线可用)。
- **持久化**:任务与悬浮球位置保存在 `%LOCALAPPDATA%\AgentTodoFloat\`,重启不丢。
- **单实例保护**:重复启动会提示「已经在运行」,防止两个实例互相覆盖任务数据。
- **多显示器 / 小屏幕适配**:面板、悬浮球、提示框均按全部显示器的实际范围定位;屏幕过矮时面板自动收缩,控件不会被裁掉。

## 快速开始

要求:Windows 10/11,Python 3.11+(含 tkinter)。已配置 Hermes CLI(`hermes` 命令可用)可获得完整 AI 分析能力;没有也能用(规则解析降级)。

```bat
rem 首次安装(在本目录):
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

rem 启动悬浮球:
run.bat

rem 开机自启(可选):
.venv\Scripts\python main.py --install-startup
```

## 使用

1. 启动后屏幕右侧出现悬浮球(可拖动)。
2. 鼠标移上去 → 展开 dashboard;移开自动收起。双击悬浮球或点面板右上「◎」可固定面板。
3. 拖入文件/图片到「拖入 图片 / 文件 到这里」区域,或在输入框输入文字:
   - 「AI 分析」/ 回车 → agent 分析生成任务(每条含标题、截止时间、优先级);
   - 「+」→ 不走 AI,按行直接添加。
4. 任务行:点击圆圈切换完成;点击进度条 +25%,右键 -25%;「✕」删除。
5. 右键悬浮球:显示/隐藏面板、固定面板、清空已完成、重置位置、退出。
6. 界面上每个按钮(悬浮球、勾选圈、进度条、✕、「AI 分析」、「+」、◎ 等)鼠标悬停都会显示功能提示。

## AI 分析说明

- 调用方式:`hermes chat --query-file <内容> [--image <图片>] -Q --format text --max-turns 6`,由后台线程执行,不阻塞界面;同一时间串行排队分析。
- 面板底部「AI 分析」勾选框随时开关;关闭后仅用规则解析(速度最快,离线可用)。
- 分析结果为 `{"tasks":[{"title","detail","due","priority","progress"}]}`,本地校验后入库;与已有任务同名的自动去重。

## 配置与数据

| 位置 | 内容 |
|---|---|
| `%LOCALAPPDATA%\AgentTodoFloat\todos.json` | 任务清单(含进度;损坏时自动备份为 .bak) |
| `%LOCALAPPDATA%\AgentTodoFloat\config.json` | 悬浮球位置、AI 开关、面板固定状态 |
| `%LOCALAPPDATA%\AgentTodoFloat\lock.pid` | 单实例锁(退出时自动清理) |

## 命令行

```
python main.py                    启动
python main.py --smoke            自检(演示文本/拖放/进度并截图退出)
python main.py --smoke --smoke-agent   自检并走真实 agent 分析
python main.py --no-agent         本次启动关闭 AI 分析
python main.py --reset-position   重置悬浮球位置
python main.py --install-startup / --uninstall-startup   管理开机自启
python main.py --data-dir <dir>   指定数据目录(测试用)
```

## 测试

```bat
.venv\Scripts\python -m unittest tests.test_offline -v
```

覆盖:任务存储/持久化/进度统计、JSON 提取(代码块/带噪声)、规则解析(中英文、截止时间、优先级)、文件内容抽取(GBK/UTF-8/docx/二进制识别)、agent 命令构造与降级。

## 目录结构

```
agent-todo-float/
  main.py            入口(参数、开机自启、自检)
  run.bat            启动器(无控制台窗口)
  app/
    app.py           控制器:悬停逻辑、分析队列、配置持久化
    ui.py            悬浮球 + dashboard + 任务行(tkinter/tkdnd)
    analyzer.py      agent 分析(hermes chat)+ 规则降级 + JSON 解析
    content.py       拖入内容的文本抽取(txt/docx/图片/二进制)
    store.py         任务模型 + JSON 存储 + 进度统计
    theme.py         配色与圆角绘制
    const.py         路径与常量
  tests/test_offline.py
```

## 卸载

1. `python main.py --uninstall-startup`(如设置过开机自启)
2. 删除程序目录与 `%LOCALAPPDATA%\AgentTodoFloat\`
