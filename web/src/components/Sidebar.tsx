import { useState } from 'react'
import {
  ChevronDown, ChevronLeft, ChevronRight, Folder, FolderPlus, MessageSquarePlus,
  Search, Settings, Trash2,
} from 'lucide-react'
import { IconButton } from './IconButton'
import { DirectoryBrowser } from './DirectoryBrowser'
import { formatTime } from '../format'
import type { SessionMeta, Workspace } from '../types'
import css from './Sidebar.module.css'

interface SidebarProps {
  collapsed: boolean
  onToggleCollapsed: () => void
  workspaces: Workspace[]
  workspace: Workspace | null
  onSelectWorkspace: (id: string) => void
  onAddWorkspace: (path: string) => Promise<void>
  onDeleteWorkspace: (id: string) => Promise<void>
  onNewChat: () => void
  sessions: SessionMeta[]
  sessionId: string | null
  onOpenSession: (id: string) => void
  sessionQuery: string
  onSessionQueryChange: (value: string) => void
  onClearHistory: () => void
  clearDisabled: boolean
  onOpenSettings: () => void
  connected: boolean
  connectionFailed: boolean
}

export function Sidebar({
  collapsed, onToggleCollapsed, workspaces, workspace, onSelectWorkspace,
  onAddWorkspace, onDeleteWorkspace, onNewChat, sessions, sessionId, onOpenSession,
  sessionQuery, onSessionQueryChange, onClearHistory, clearDisabled, onOpenSettings,
  connected, connectionFailed,
}: SidebarProps) {
  const [browserOpen, setBrowserOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const visibleSessions = sessions.filter(session =>
    (session.summary || '未命名会话').toLowerCase().includes(sessionQuery.toLowerCase()),
  )

  const handleDelete = async () => {
    if (!workspace) return
    setConfirmDelete(false)
    await onDeleteWorkspace(workspace.id)
  }

  return (
    <aside className={css.sidebar}>
      <div className={css.sideTop}>
        <div className={css.brand}>
          <img src="/windcode-neon-wind-core.svg" alt="" />
          <strong>Windcode</strong>
        </div>
        <IconButton label={collapsed ? '展开侧栏' : '折叠侧栏'} onClick={onToggleCollapsed}>
          {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
        </IconButton>
      </div>

      <button className={css.newChat} disabled={!workspace} onClick={onNewChat}>
        <MessageSquarePlus size={17} />
        <span>新会话</span>
      </button>

      <div className={css.workspaceRow}>
        <div className={css.workspaceSelect} title={workspace?.path ?? ''}>
          <Folder size={16} />
          <select
            value={workspace?.id ?? ''}
            onChange={event => onSelectWorkspace(event.target.value)}
            aria-label="工作区"
            disabled={!workspace}
          >
            {workspace
              ? workspaces.map(item => <option key={item.id} value={item.id}>{item.name}</option>)
              : <option value="">在聊天区添加工作区</option>}
          </select>
          <ChevronDown size={14} />
        </div>
        <IconButton label="添加工作区" className={css.workspaceAction} onClick={() => setBrowserOpen(true)}><FolderPlus size={16} /></IconButton>
        {workspace && (
          <IconButton
            label={confirmDelete ? '再次点击确认删除' : '删除当前工作区'}
            className={`${css.workspaceAction} ${confirmDelete ? css.dangerActive : css.deleteBtn}`}
            onClick={() => (confirmDelete ? handleDelete() : setConfirmDelete(true))}
          >
            <Trash2 size={16} />
          </IconButton>
        )}
      </div>
      {confirmDelete && (
        <div className={css.deleteConfirm} role="alert">
          <span>确认删除工作区「{workspace?.name}」？</span>
          <button type="button" onClick={() => void handleDelete()}>删除</button>
          <button type="button" onClick={() => setConfirmDelete(false)}>取消</button>
        </div>
      )}

      <div className={css.sessionTools}>
        <span>会话</span>
        <button
          className={css.clearHistory}
          disabled={clearDisabled}
          onClick={onClearHistory}
          title="清空历史记录"
          aria-label="清空历史记录"
        >
          <Trash2 size={13} />
          <span>清空</span>
        </button>
      </div>
      <label className={css.sessionSearch}>
        <Search size={14} />
        <input
          value={sessionQuery}
          onChange={event => onSessionQueryChange(event.target.value)}
          placeholder="搜索会话"
          aria-label="搜索会话"
        />
      </label>

      <nav className={css.sessions} aria-label="会话列表">
        {visibleSessions.map(session => (
          <button
            key={session.session_id}
            data-active={sessionId === session.session_id || undefined}
            onClick={() => onOpenSession(session.session_id)}
          >
            <span>{session.summary || '未命名会话'}</span>
            <time>{formatTime(session.updated_at)}</time>
          </button>
        ))}
      </nav>

      <div className={css.sideBottom}>
        <button disabled={!workspace} onClick={onOpenSettings}>
          <Settings size={17} />
          <span>设置</span>
        </button>
        <span
          className={css.connection}
          data-online={connected || undefined}
          data-failed={connectionFailed || undefined}
        >
          {workspace ? (connected ? '已连接' : connectionFailed ? '连接失败 · 重试中' : '正在连接') : '等待工作区'}
        </span>
      </div>

      {browserOpen && (
        <DirectoryBrowser
          onClose={() => setBrowserOpen(false)}
          onSelect={async path => {
            setBrowserOpen(false)
            await onAddWorkspace(path)
          }}
        />
      )}
    </aside>
  )
}
