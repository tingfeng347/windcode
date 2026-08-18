import { useCallback, useEffect, useRef, useState } from 'react'
import { Menu, PanelRightClose } from 'lucide-react'
import { Chat } from './components/Chat'
import { Composer } from './components/Composer'
import { DetailsPanel } from './components/DetailsPanel'
import { SettingsPanel } from './components/SettingsPanel'
import { Sidebar } from './components/Sidebar'
import { IconButton } from './components/IconButton'
import { api, json } from './api'
import { applyEnvelope, recordsToTranscript } from './transcript'
import { useTheme } from './theme'
import type {
  Bootstrap, EventEnvelope, PermissionMode, SessionMeta, SessionRecord, SlashCommand,
  TranscriptItem, Workspace,
} from './types'
import css from './App.module.css'

type ApprovalItem = Extract<TranscriptItem, { type: 'approval' }>
type QuestionItem = Extract<TranscriptItem, { type: 'question' }>

const commandBlocklistWhileRunning = ['new', 'resume', 'rewind', 'model', 'extensions', 'memory']

export function App() {
  const [theme, setTheme] = useTheme()
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null)
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [items, setItems] = useState<TranscriptItem[]>([])
  const [prompt, setPrompt] = useState('')
  const [activeRun, setActiveRun] = useState<string | null>(null)
  const [permission, setPermission] = useState<PermissionMode>('default')
  const [model, setModel] = useState('')
  const [collapsed, setCollapsed] = useState(false)
  const [settings, setSettings] = useState(false)
  const [detail, setDetail] = useState<TranscriptItem | null>(null)
  const [connected, setConnected] = useState(false)
  const [connectionFailed, setConnectionFailed] = useState(false)
  const [sessionQuery, setSessionQuery] = useState('')
  const [error, setError] = useState('')
  const [commands, setCommands] = useState<SlashCommand[]>([])
  const streamSequence = useRef(0)

  const loadWorkspace = useCallback(async (id: string) => {
    const [nextBootstrap, nextSessions, nextCommands] = await Promise.all([
      api<Bootstrap>(`/api/v1/bootstrap?workspace_id=${id}`),
      api<SessionMeta[]>(`/api/v1/workspaces/${id}/sessions`),
      api<{ items: SlashCommand[] }>(`/api/v1/workspaces/${id}/commands`),
    ])
    setBootstrap(nextBootstrap); setWorkspace(nextBootstrap.workspace); setWorkspaces(nextBootstrap.workspaces)
    setSessions(nextSessions); setPermission(nextBootstrap.permission_mode)
    setCommands(nextCommands.items)
    const defaultProvider = nextBootstrap.providers.find(item => item.is_default) ?? nextBootstrap.providers[0]
    setModel(defaultProvider?.alias ?? '')
  }, [])

  const refreshBootstrap = useCallback(async () => { if (workspace) await loadWorkspace(workspace.id) }, [loadWorkspace, workspace])

  useEffect(() => {
    void api<{ selected?: string; items: Workspace[] }>('/api/v1/workspaces').then(async value => {
      setWorkspaces(value.items)
      if (value.selected) await loadWorkspace(value.selected)
    }).catch(value => setError(String(value)))
  }, [loadWorkspace])

  useEffect(() => {
    if (!workspace) return
    let closed = false
    let socket: WebSocket | null = null
    let retry: number | undefined
    const connect = () => {
      const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
      const eventHost = location.port === '5173' ? '127.0.0.1:8765' : location.host
      socket = new WebSocket(`${protocol}://${eventHost}/api/v1/events?workspace_id=${workspace.id}&after=${streamSequence.current}`)
      socket.onopen = () => { setConnected(true); setConnectionFailed(false) }
      socket.onmessage = event => {
        const envelope = JSON.parse(event.data) as EventEnvelope
        streamSequence.current = Math.max(streamSequence.current, envelope.stream_sequence)
        setItems(current => applyEnvelope(current, envelope))
        if (envelope.type === 'run.finished' || (envelope.type === 'run.event' && ['run_failed', 'run_cancelled', 'run_completed'].includes(String(envelope.payload.kind)))) {
          setActiveRun(null)
          void api<SessionMeta[]>(`/api/v1/workspaces/${workspace.id}/sessions`).then(setSessions)
        }
      }
      socket.onerror = () => {
        if (closed) return
        setConnected(false)
        setConnectionFailed(true)
        setError('实时连接失败，正在重试。请确认 Windcode Web 服务正在运行。')
      }
      socket.onclose = () => {
        if (closed) return
        setConnected(false)
        retry = window.setTimeout(connect, 1200)
      }
    }
    connect()
    return () => { closed = true; if (retry) clearTimeout(retry); socket?.close() }
  }, [workspace])

  const addWorkspace = async (path: string) => {
    const entry = await api<Workspace>('/api/v1/workspaces', json('POST', { path }))
    await loadWorkspace(entry.id)
  }

  const deleteWorkspace = async (id: string) => {
    await api(`/api/v1/workspaces/${id}`, json('DELETE', {}))
    setSessionId(null); setItems([]); setActiveRun(null); setDetail(null); streamSequence.current = 0
    const remaining = await api<{ selected?: string | null; items: Workspace[] }>('/api/v1/workspaces')
    setWorkspaces(remaining.items)
    if (remaining.selected) {
      await loadWorkspace(remaining.selected)
    } else {
      setWorkspace(null); setBootstrap(null); setSessions([])
    }
  }

  const selectWorkspace = async (id: string) => {
    await api(`/api/v1/workspaces/${id}/select`, json('POST'))
    setSessionId(null); setItems([]); setActiveRun(null); streamSequence.current = 0
    await loadWorkspace(id)
  }

  const openSession = async (id: string) => {
    if (!workspace) return
    const value = await api<{ records: SessionRecord[] }>(`/api/v1/workspaces/${workspace.id}/sessions/${id}`)
    setSessionId(id); setItems(recordsToTranscript(value.records)); setDetail(null)
  }

  const newChat = () => { setSessionId(null); setItems([]); setActiveRun(null); setDetail(null) }

  const clearHistory = async () => {
    if (!workspace || activeRun || sessions.length === 0) return
    if (!window.confirm('确定清空当前工作区的全部会话历史吗？此操作无法撤销。')) return
    try {
      await api(`/api/v1/workspaces/${workspace.id}/sessions`, json('DELETE'))
      setSessions([]); setSessionId(null); setItems([]); setDetail(null); setError('')
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    }
  }

  const showNotice = (text: string, tone: 'error' | 'info' = 'info') => {
    setItems(current => [...current, { id: `command-${Date.now()}`, type: 'notice', tone, text }])
  }

  const executeCommand = async (value: string) => {
    if (!workspace) return
    const [rawName, ...argumentsList] = value.slice(1).trim().split(/\s+/)
    const name = rawName.toLowerCase()
    const command = commands.find(item => item.name === name)
    if (!command) { showNotice(`未知命令: /${name}`, 'error'); return }
    if (activeRun && commandBlocklistWhileRunning.includes(name)) {
      showNotice(`任务运行期间不能执行 /${name}`, 'error'); return
    }
    if (command.target) {
      const [kind, selector] = command.target.split(':', 2)
      const prefix = kind === 'skill' ? '$' : kind === 'prompt' ? '@prompt:' : '@capability:'
      setPrompt([`${prefix}${selector}`, ...argumentsList].join(' '))
      return
    }
    if (name === 'new') { newChat(); showNotice('已新建会话'); return }
    if (name === 'clear') { setItems([]); return }
    if (name === 'resume') {
      if (argumentsList.length !== 1) { showNotice('用法: /resume 会话ID', 'error'); return }
      const selected = sessions.find(item => item.session_id.startsWith(argumentsList[0]))
      if (!selected) { showNotice('未找到匹配会话', 'error'); return }
      await openSession(selected.session_id); return
    }
    if (name === 'rewind') { showNotice('请在左侧选择会话后，从历史记录中回退（Web 回退选择器即将提供）'); return }
    if (name === 'model') {
      if (argumentsList.length > 1) { showNotice('用法: /model [配置别名]', 'error'); return }
      if (argumentsList[0]) {
        if (!bootstrap?.providers.some(item => item.alias === argumentsList[0])) { showNotice(`未知模型: ${argumentsList[0]}`, 'error'); return }
        setModel(argumentsList[0]); showNotice(`已切换模型: ${argumentsList[0]}`)
      } else setSettings(true)
      return
    }
    if (name === 'compact') {
      if (argumentsList.length) { showNotice('用法: /compact', 'error'); return }
      if (!activeRun) { showNotice('压缩需要在任务运行期间执行', 'error'); return }
      await api(`/api/v1/workspaces/${workspace.id}/runs/${activeRun}/compact`, json('POST')); showNotice('已请求压缩上下文'); return
    }
    if (name === 'status') { showNotice(`会话: ${sessionId ?? '新会话'} · 模型: ${model || '未选择'} · 权限: ${permission} · 实时连接: ${connected ? '已连接' : '未连接'}`); return }
    if (name === 'agents') { showNotice(activeRun ? '子智能体状态将在运行详情中显示' : '当前没有子智能体任务'); return }
    if (name === 'extensions') { setSettings(true); return }
    if (name === 'memory') { showNotice('长期记忆管理暂未在 Web 设置中提供，请使用 TUI 的 /memory'); return }
    if (name === 'quit') { showNotice('Web 界面不能关闭浏览器标签；请关闭此标签页或停止 Windcode Web 服务'); return }
    if (name === 'help') showNotice(`可用命令:\n${commands.map(item => `/${item.name}  ${item.description}`).join('\n')}`)
  }

  const send = async () => {
    if (!workspace || !prompt.trim() || activeRun) return
    const text = prompt.trim(); setPrompt(''); setError('')
    if (text.startsWith('/')) {
      await executeCommand(text)
      return
    }
    setItems(current => [...current, { id: `pending-${Date.now()}`, type: 'user', text }])
    try {
      const run = await api<{ session_id: string; run_id: string }>(`/api/v1/workspaces/${workspace.id}/runs`, json('POST', { prompt: text, session_id: sessionId, model: model || null, permission_mode: permission }))
      setSessionId(run.session_id); setActiveRun(run.run_id)
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value)); setActiveRun(null)
    }
  }

  const cancelRun = () => {
    if (workspace && activeRun) void api(`/api/v1/workspaces/${workspace.id}/runs/${activeRun}/cancel`, json('POST'))
  }

  const respondApproval = async (item: ApprovalItem, decision: string) => {
    if (!workspace || !activeRun) return
    await api(`/api/v1/workspaces/${workspace.id}/runs/${activeRun}/approval`, json('POST', { request_id: item.requestId, decision }))
    setItems(current => current.filter(value => value.id !== item.id))
  }

  const respondQuestions = async (item: QuestionItem, form: HTMLFormElement) => {
    if (!workspace || !activeRun) return
    const answers = Object.fromEntries(new FormData(form).entries())
    await api(`/api/v1/workspaces/${workspace.id}/runs/${activeRun}/answers`, json('POST', { request_id: item.requestId, answers }))
    setItems(current => current.filter(value => value.id !== item.id))
  }

  const changePermission = async (mode: PermissionMode) => {
    setPermission(mode)
    if (workspace && activeRun) await api(`/api/v1/workspaces/${workspace.id}/runs/${activeRun}/permission`, json('PATCH', { mode }))
  }

  const sessionTitle = workspace ? sessions.find(item => item.session_id === sessionId)?.summary || '新会话' : '新会话'

  return (
    <div className={css.app} data-sidebar={collapsed ? 'collapsed' : 'open'} data-details={detail ? 'open' : undefined}>
      <Sidebar
        collapsed={collapsed}
        onToggleCollapsed={() => setCollapsed(value => !value)}
        workspaces={workspaces}
        workspace={workspace}
        onSelectWorkspace={id => void selectWorkspace(id)}
        onAddWorkspace={addWorkspace}
        onDeleteWorkspace={deleteWorkspace}
        onNewChat={newChat}
        sessions={sessions}
        sessionId={sessionId}
        onOpenSession={id => void openSession(id)}
        sessionQuery={sessionQuery}
        onSessionQueryChange={setSessionQuery}
        onClearHistory={() => void clearHistory()}
        clearDisabled={activeRun !== null || sessions.length === 0}
        onOpenSettings={() => setSettings(true)}
        connected={connected}
        connectionFailed={connectionFailed}
      />
      <main className={css.main}>
        <header className={css.topbar}>
          <div>
            <IconButton label="菜单" onClick={() => setCollapsed(value => !value)}><Menu size={18} /></IconButton>
            <div>
              <strong>{sessionTitle}</strong>
              <span>{workspace?.path ?? '先在下方添加本机项目目录'}</span>
            </div>
          </div>
          <div>
            {workspace && (
              <span className={css.modelState} data-ready={bootstrap?.model_ready || undefined}>
                {bootstrap?.model_ready ? model || '模型就绪' : '需要配置模型'}
              </span>
            )}
            {detail && <IconButton label="关闭详情" onClick={() => setDetail(null)}><PanelRightClose size={18} /></IconButton>}
          </div>
        </header>
        <Chat
          hasWorkspace={!!workspace}
          items={items}
          activeRun={activeRun}
          onAddWorkspace={addWorkspace}
          onApproval={(item, decision) => void respondApproval(item, decision)}
          onQuestions={(item, form) => void respondQuestions(item, form)}
          onOpenDetail={setDetail}
          onSuggestion={setPrompt}
        />
        {workspace && (
          <Composer
            prompt={prompt}
            onPromptChange={setPrompt}
            commands={commands}
            model={model}
            onModelChange={setModel}
            providers={bootstrap?.providers ?? []}
            permission={permission}
            onPermissionChange={mode => void changePermission(mode)}
            modelReady={!!bootstrap?.model_ready}
            activeRun={activeRun}
            error={error}
            onSend={() => void send()}
            onCancelRun={cancelRun}
          />
        )}
      </main>
      <DetailsPanel detail={detail} onClose={() => setDetail(null)} />
      {settings && workspace && bootstrap && (
        <SettingsPanel
          workspace={workspace}
          bootstrap={bootstrap}
          theme={theme}
          setTheme={setTheme}
          onClose={() => setSettings(false)}
          onChanged={refreshBootstrap}
        />
      )}
    </div>
  )
}
