import { CircleStop, Send, Shield } from 'lucide-react'
import type { PermissionMode, Provider, SlashCommand } from '../types'
import css from './Composer.module.css'

const permissionLabels: Record<PermissionMode, string> = {
  plan: '计划', default: '默认', accept_edits: '接受编辑', full_access: '完全访问',
}

interface ComposerProps {
  prompt: string
  onPromptChange: (value: string) => void
  commands: SlashCommand[]
  model: string
  onModelChange: (value: string) => void
  providers: Provider[]
  permission: PermissionMode
  onPermissionChange: (mode: PermissionMode) => void
  modelReady: boolean
  activeRun: string | null
  error: string
  onSend: () => void
  onCancelRun: () => void
}

export function Composer({
  prompt, onPromptChange, commands, model, onModelChange, providers, permission, onPermissionChange,
  modelReady, activeRun, error, onSend, onCancelRun,
}: ComposerProps) {
  const commandMatches = prompt.startsWith('/') && !prompt.includes(' ')
    ? commands.filter(command => command.name.startsWith(prompt.slice(1).toLowerCase()))
    : []

  return (
    <footer className={css.composerWrap}>
      <div className={css.composer} data-disabled={!modelReady || undefined}>
        {commandMatches.length > 0 && (
          <div className={css.commandMenu} role="listbox" aria-label="命令建议">
            {commandMatches.map(command => (
              <button type="button" key={command.name} onClick={() => onPromptChange(`/${command.name} `)}>
                <code>/{command.name}</code>
                <span>{command.description}</span>
              </button>
            ))}
          </div>
        )}
        <textarea
          value={prompt}
          onChange={event => onPromptChange(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Tab' && commandMatches.length) {
              event.preventDefault()
              onPromptChange(`/${commandMatches[0].name} `)
            }
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              onSend()
            }
          }}
          placeholder={modelReady ? '向 Windcode 发送任务，输入 / 使用命令' : '请先在设置中配置模型'}
          disabled={!modelReady}
          rows={1}
        />
        <div className={css.composerBar}>
          <div>
            <select value={model} onChange={event => onModelChange(event.target.value)} aria-label="模型">
              {providers.map(provider => (
                <option value={provider.alias} key={provider.alias}>{provider.alias} · {provider.provider.model}</option>
              ))}
            </select>
            <div className={css.permissionMenu}>
              <Shield size={14} />
              <select
                value={permission}
                onChange={event => onPermissionChange(event.target.value as PermissionMode)}
                aria-label="权限模式"
              >
                {Object.entries(permissionLabels).map(([id, label]) => <option value={id} key={id}>{label}</option>)}
              </select>
            </div>
          </div>
          {activeRun ? (
            <button className={css.stopButton} onClick={onCancelRun} aria-label="停止">
              <CircleStop size={18} />
            </button>
          ) : (
            <button className={css.sendButton} onClick={onSend} disabled={!prompt.trim() || !modelReady} aria-label="发送">
              <Send size={18} />
            </button>
          )}
        </div>
      </div>
      {error && <div className={css.errorLine}>{error}</div>}
    </footer>
  )
}
