/**
 * AgentTodo Switch — Hermes Desktop 状态栏开关。
 *
 * 点击状态栏芯片(或 ⌘K「AgentTodo: 启动/停止悬浮待办」)即可启动/停止
 * E:\AgentTodo 桌面悬浮待办球,并实时显示 任务完成数 / 总进度 / AI 开关状态。
 *
 * 数据通道: ctx.rest → GET/POST /api/plugins/agenttodo-switch/{status,start,stop}
 * 后端: E:\Hermes\plugins\agenttodo-switch\dashboard\plugin_api.py
 *       (首次请求自动热挂载;后端未启用时返回 404 → 芯片显示 ⚠)
 *
 * Plain ESM, loaded uncompiled: jsx() calls, no JSX syntax.
 * Only @hermes/plugin-sdk / react / react/jsx-runtime resolve.
 */
import { cn, haptic, host, Tip, PALETTE_AREA } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useCallback, useEffect, useRef, useState } from 'react'

const EMPTY = {
  running: false, pids: [], total: 0, done: 0, done_today: 0, progress: 0, overall: 0,
  overdue: 0, due_today: 0, analyzing: false, queue: 0, ai: null,
  last_note: '', last_error: '',
  // false, not true: a legacy backend (pre status.json) omits the key, and a
  // missing key must read as "unknown", never "hung".
  stale: false
}
const POLL_MS = 5000

function errText(e) {
  return e && e.message ? String(e.message) : String(e)
}

/** Shared toggle used by both the chip and the palette command. */
async function doToggle(rest) {
  const st = await rest('/status')
  const res = await rest(st && st.running ? '/stop' : '/start', { method: 'POST' })
  host.notify({
    kind: 'info',
    message: res && res.running ? 'AgentTodo 已启动' : 'AgentTodo 已停止'
  })
  return res
}

function Chip({ rest }) {
  const [st, setSt] = useState(EMPTY)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const alive = useRef(true)

  const load = useCallback(async () => {
    try {
      const s = await rest('/status')
      if (!alive.current) return
      setSt({ ...EMPTY, ...(s || {}) })
      setErr('')
    } catch (e) {
      if (!alive.current) return
      setErr(errText(e))
    }
  }, [rest])

  useEffect(() => {
    alive.current = true
    void load()
    const id = setInterval(() => { void load() }, POLL_MS)
    return () => { alive.current = false; clearInterval(id) }
  }, [load])

  const toggle = async () => {
    if (busy) return
    haptic('tap')
    setBusy(true)
    try {
      const res = await doToggle(rest)
      setSt({ ...EMPTY, ...(res || {}) })
      setErr(res && res.error ? String(res.error) : '')
      setTimeout(() => { void load() }, 1500)
    } catch (e) {
      setErr(errText(e))
      host.notifyError('AgentTodo 开关失败: ' + errText(e))
    } finally {
      setBusy(false)
    }
  }

  const on = !!st.running
  const pct = st.overall ?? st.progress ?? 0
  const overdue = Number(st.overdue || 0)
  const dueToday = Number(st.due_today || 0)
  const doneToday = Number(st.done_today || 0)
  const label = err
    ? '待办 ⚠'
    : !on
      ? (st.total ? `待办 ${pct}% · 关` : '待办 关')
      : st.stale
        ? '待办 无响应'
        : st.analyzing
          ? `待办 分析中${st.queue ? ` +${st.queue}` : ''}…`
          : overdue
            ? `待办 ${overdue} 逾期`
            : dueToday
              ? `待办 ${dueToday} 今天`
              : (doneToday && !st.total)
                ? `待办 今日完成 ${doneToday}`
                : `待办 ${st.done}/${st.total} · ${pct}%`

  const counts = `任务 ${st.done}/${st.total} · 总进度 ${pct}%${
    doneToday ? ` · 今日完成 ${doneToday}` : ''}${
    overdue ? ` · 逾期 ${overdue}` : ''}${dueToday ? ` · 今天到期 ${dueToday}` : ''}`
  const aiLine = st.ai === false ? 'AI 分析已关闭(规则解析)' : 'AI 分析开启'
  const errLine = st.last_error ? `\n最近一次分析失败: ${st.last_error}` : ''
  const tip = err
    ? `AgentTodo 后端不可用: ${err}(检查 config plugins.enabled / 是否已 Reload desktop plugins)`
    : on
      ? `AgentTodo 运行中 · ${counts} · ${aiLine}${
          st.analyzing ? '\n正在分析拖入的内容…' : ''}${errLine}\n点击停止悬浮球`
      : `${st.total ? `${counts}\n` : ''}AgentTodo 未运行 · 点击启动悬浮球(拖文件/文字给它,AI 自动提待办)`

  return jsx(Tip, {
    label: tip,
    children: jsx('button', {
      type: 'button',
      'aria-label': 'AgentTodo 开关',
      disabled: busy,
      onClick: toggle,
      className: cn(
        'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] transition-colors',
        'hover:bg-(--chrome-action-hover) disabled:opacity-60',
        on ? 'text-foreground' : 'text-(--ui-text-quaternary)'
      ),
      children: jsxs('span', {
        className: 'inline-flex items-center gap-1',
        children: [
          jsx('span', {
            style: { color: on ? 'var(--ui-accent)' : 'var(--ui-text-quaternary)' },
            children: on ? '●' : '○'
          }),
          jsx('span', { children: label })
        ]
      })
    })
  })
}

export default {
  id: 'agenttodo-switch',
  name: 'AgentTodo Switch',
  register(ctx) {
    const rest = (path, opts) => ctx.rest(path, opts)

    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 140,
      render: () => jsx(Chip, { rest })
    })

    ctx.register({
      id: 'toggle',
      area: PALETTE_AREA,
      data: {
        id: 'agenttodo.toggle',
        label: 'AgentTodo: 启动/停止 悬浮待办',
        keywords: ['agenttodo', '待办', 'todo', '悬浮球'],
        run: () => { void doToggle(rest).catch(e => host.notifyError('AgentTodo 开关失败: ' + errText(e))) }
      }
    })
  }
}
