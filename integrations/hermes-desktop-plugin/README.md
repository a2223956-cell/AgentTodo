# Hermes 桌面状态栏开关(AgentTodo Switch)

这是 AgentTodo 的 Hermes Desktop 集成件的**镜像副本**。运行时 Hermes 从下面两个位置加载,本目录只用于版本管理与备份:

| 本仓库文件 | 运行时位置 |
|---|---|
| `plugin.js` | `%HERMES_HOME%\desktop-plugins\agenttodo-switch\plugin.js` |
| `plugin.yaml`、`dashboard/plugin_api.py` | `%HERMES_HOME%\plugins\agenttodo-switch\` |

本机 `HERMES_HOME=E:\Hermes`。桌面端监听 `desktop-plugins/`,保存即热加载;Python 半边(`plugin_api.py`)在首次请求时热挂载,**再次修改必须重启桌面端**才会生效。

## 启用

1. 两个位置的文件与上表一致;
2. `config.yaml` 的 `plugins.enabled` 里列出 `agenttodo-switch`(Trust gate);
3. ⌘K → **Reload desktop plugins**。

## 功能

- 状态栏芯片:左键启动/停止 `E:\AgentTodo` 悬浮球;显示进度、逾期/今日到期、今日完成(`done_today`)与分析中状态。
- ⌘K 命令:`AgentTodo: 启动/停止 悬浮待办`。
- 后端:`GET /api/plugins/agenttodo-switch/status`、`POST /start`、`POST /stop`。状态读应用写的 `status.json` 心跳(每 5 s),不做进程扫描。

## 与 AgentTodo 的数据契约

`status.json`(`%LOCALAPPDATA%\AgentTodoFloat\`):

```json
{"running": true, "pid": 1234, "total": 7, "done": 0, "done_today": 2,
 "overall": 40, "overdue": 0, "due_today": 1, "analyzing": false, "queue": 0,
 "ai": true, "last_note": "", "last_error": "", "updated_at": 1793159000.0}
```

- 心跳超过 20 s 视为未运行;干净退出写 `running:false`。
- 应用未运行时,后端回退读 `todos.json` + `completed.json`,计算同样的字段。

> 同步规则:改完这里的副本要拷到 `%HERMES_HOME%`;反之在运行时位置改完也要立即回拷并提交,避免两处漂移。
