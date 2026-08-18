import { useCallback, useEffect, useState } from 'react'
import {
  Bot, Check, ChevronDown, ChevronUp, CircleAlert, FolderPlus, Gauge, Languages, Moon, Play,
  Plug, Plus, Search, Sparkles, Sun, Terminal, Trash2, X,
} from 'lucide-react'
import { IconButton } from './IconButton'
import { api, json } from '../api'
import type { Theme } from '../theme'
import type {
  Bootstrap, ExtensionRecord, Provider, Workspace,
} from '../types'
import css from './SettingsPanel.module.css'

type SettingsTab = 'models' | 'plugins' | 'skills' | 'mcp' | 'general'

interface SettingsPanelProps {
  workspace: Workspace
  bootstrap: Bootstrap
  theme: Theme
  setTheme: (theme: Theme) => void
  onClose: () => void
  onChanged: () => Promise<void>
}

const tabTitles: Record<SettingsTab, string> = {
  models: '模型', plugins: '插件', skills: 'Skills', mcp: 'MCP Servers', general: '通用',
}

export function SettingsPanel({ workspace, bootstrap, theme, setTheme, onClose, onChanged }: SettingsPanelProps) {
  const [tab, setTab] = useState<SettingsTab>('models')
  const [providers, setProviders] = useState<Provider[]>(bootstrap.providers)
  const [extensions, setExtensions] = useState<ExtensionRecord[]>([])
  const [mcp, setMcp] = useState<{ servers: Record<string, Record<string, unknown>>; states: Record<string, string> }>({ servers: {}, states: {} })
  const [query, setQuery] = useState('')
  const [message, setMessage] = useState('')
  const [fallbackChain, setFallbackChain] = useState<string[]>(bootstrap.fallback_chain)
  const [providerForm, setProviderForm] = useState({ alias: '', protocol: 'openai_compatible', model: '', base_url: '', api_key_env: '', secret: '', editing_alias: '' })
  const [pluginPath, setPluginPath] = useState('')
  const [skillRoot, setSkillRoot] = useState('')
  const [mcpForm, setMcpForm] = useState({ id: '', transport: 'stdio', command: '', args: '', url: '', required: false })

  const refresh = useCallback(async () => {
    const [nextProviders, nextExtensions, nextMcp] = await Promise.all([
      api<Provider[]>(`/api/v1/workspaces/${workspace.id}/providers`),
      api<ExtensionRecord[]>(`/api/v1/workspaces/${workspace.id}/extensions`),
      api<typeof mcp>(`/api/v1/workspaces/${workspace.id}/mcp`),
    ])
    setProviders(nextProviders)
    setExtensions(nextExtensions)
    setMcp(nextMcp)
  }, [workspace.id])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => { setFallbackChain(bootstrap.fallback_chain) }, [bootstrap.fallback_chain])

  const run = async (action: () => Promise<unknown>, success: string) => {
    setMessage('')
    try {
      await action()
      await refresh()
      await onChanged()
      setMessage(success)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    }
  }

  const filtered = extensions.filter(item => {
    const matches = `${item.public_name} ${item.capability_id}`.toLowerCase().includes(query.toLowerCase())
    if (!matches) return false
    if (tab === 'plugins') return item.kind === 'plugin'
    if (tab === 'skills') return item.kind === 'skill'
    return true
  })

  const saveProvider = () => run(async () => {
    const alias = providerForm.alias.trim()
    await api(`/api/v1/workspaces/${workspace.id}/providers/${encodeURIComponent(alias)}`, json('PUT', {
      alias,
      protocol: providerForm.protocol,
      model: providerForm.model,
      provider_id: alias,
      api_key_env: providerForm.api_key_env || null,
      base_url: providerForm.base_url || null,
      secret: providerForm.secret || null,
      editing_alias: providerForm.editing_alias || null,
    }))
    setProviderForm({ alias: '', protocol: 'openai_compatible', model: '', base_url: '', api_key_env: '', secret: '', editing_alias: '' })
  }, 'Provider 已保存')

  const saveFallback = (aliases: string[]) => run(async () => {
    await api(`/api/v1/workspaces/${workspace.id}/provider-chain/fallback`, json('PUT', { aliases }))
    setFallbackChain(aliases)
  }, 'Fallback 顺序已更新')

  const moveFallback = (alias: string, offset: number) => {
    const index = fallbackChain.indexOf(alias)
    const target = index + offset
    if (index < 0 || target < 0 || target >= fallbackChain.length) return
    const next = [...fallbackChain]
    ;[next[index], next[target]] = [next[target], next[index]]
    void saveFallback(next)
  }

  const saveMcp = () => run(async () => {
    const body = mcpForm.transport === 'stdio'
      ? { transport: 'stdio', enable: true, required: mcpForm.required, command: mcpForm.command, args: mcpForm.args.split(/\s+/).filter(Boolean), env: {} }
      : { transport: 'streamable_http', enable: true, required: mcpForm.required, url: mcpForm.url, headers: {} }
    await api(`/api/v1/workspaces/${workspace.id}/mcp/${encodeURIComponent(mcpForm.id)}`, json('PUT', body))
    setMcpForm({ id: '', transport: 'stdio', command: '', args: '', url: '', required: false })
  }, 'MCP Server 已保存')

  return (
    <div className={css.settingsBackdrop} role="dialog" aria-modal="true" aria-label="设置">
      <section className={css.settingsPanel}>
        <aside className={css.settingsNav}>
          <div className={css.settingsBrand}><img src="/windcode-neon-wind-core.svg" alt="" /><strong>设置</strong></div>
          {([
            ['models', Bot, '模型'], ['plugins', Plug, '插件'], ['skills', Sparkles, 'Skills'],
            ['mcp', Terminal, 'MCP'], ['general', Gauge, '通用'],
          ] as const).map(([id, Icon, label]) => (
            <button key={id} data-active={tab === id || undefined} onClick={() => setTab(id)}><Icon size={17} />{label}</button>
          ))}
        </aside>
        <div className={css.settingsContent}>
          <header>
            <div>
              <h2>{tabTitles[tab]}</h2>
              <p>{workspace.path}</p>
            </div>
            <IconButton label="关闭设置" onClick={onClose}><X size={18} /></IconButton>
          </header>
          {message && <div className={css.settingsMessage}>{message}</div>}

          {tab === 'models' && (
            <div className={css.settingsSection}>
              <div className={css.cardGrid}>
                {providers.map(provider => (
                  <article className={css.settingCard} key={provider.alias}>
                    <div className={css.cardHead}>
                      <div><strong>{provider.alias}</strong><span>{provider.provider.model}</span></div>
                      <i data-state={provider.status}>{provider.status}</i>
                    </div>
                    <dl>
                      <dt>协议</dt><dd>{provider.provider.protocol}</dd>
                      <dt>端点</dt><dd>{provider.provider.base_url || '默认'}</dd>
                      <dt>凭据</dt><dd>{provider.credential_source ? '已配置' : '未配置'}</dd>
                    </dl>
                    <div className={css.cardActions}>
                      {!provider.is_default && (
                        <button onClick={() => void run(() => api(`/api/v1/workspaces/${workspace.id}/providers/${provider.alias}/default`, json('POST')), '默认模型已更新')}>
                          <Check size={14} />设为默认
                        </button>
                      )}
                      <button onClick={() => setProviderForm({ alias: provider.alias, protocol: provider.provider.protocol, model: provider.provider.model, base_url: provider.provider.base_url || '', api_key_env: provider.provider.api_key_env || '', secret: '', editing_alias: provider.alias })}>编辑</button>
                      {provider.credential_source && (
                        <button onClick={() => void run(() => api(`/api/v1/workspaces/${workspace.id}/providers/${provider.alias}/credential`, json('DELETE')), 'API Key 已移除')}>移除密钥</button>
                      )}
                      <button className={css.danger} onClick={() => void run(() => api(`/api/v1/workspaces/${workspace.id}/providers/${provider.alias}`, json('DELETE')), 'Provider 已删除')}>
                        <Trash2 size={14} />删除
                      </button>
                    </div>
                  </article>
                ))}
              </div>
              {providers.some(provider => !provider.is_default) && (
                <section className={css.fallbackEditor}>
                  <div><strong>Fallback 顺序</strong><span>主模型不可用时按此顺序尝试</span></div>
                  {providers.filter(provider => !provider.is_default).map(provider => {
                    const index = fallbackChain.indexOf(provider.alias)
                    return (
                      <div key={provider.alias}>
                        <span>{provider.alias}</span>
                        {index < 0 ? (
                          <button onClick={() => void saveFallback([...fallbackChain, provider.alias])}>加入</button>
                        ) : (
                          <>
                            <small>#{index + 1}</small>
                            <IconButton label="上移" disabled={index === 0} onClick={() => moveFallback(provider.alias, -1)}><ChevronUp size={14} /></IconButton>
                            <IconButton label="下移" disabled={index === fallbackChain.length - 1} onClick={() => moveFallback(provider.alias, 1)}><ChevronDown size={14} /></IconButton>
                            <button onClick={() => void saveFallback(fallbackChain.filter(alias => alias !== provider.alias))}>移除</button>
                          </>
                        )}
                      </div>
                    )
                  })}
                </section>
              )}
              <form className={css.editorForm} onSubmit={event => { event.preventDefault(); void saveProvider() }}>
                <h3><Plus size={16} />{providerForm.editing_alias ? '编辑 Provider' : '添加 Provider'}</h3>
                <div className={css.formGrid}>
                  <label>别名<input required value={providerForm.alias} onChange={event => setProviderForm({ ...providerForm, alias: event.target.value })} /></label>
                  <label>协议<select value={providerForm.protocol} onChange={event => setProviderForm({ ...providerForm, protocol: event.target.value })}><option value="openai_compatible">OpenAI Compatible</option><option value="openai_responses">OpenAI Responses</option><option value="anthropic_messages">Anthropic Messages</option></select></label>
                  <label>模型 ID<input required value={providerForm.model} onChange={event => setProviderForm({ ...providerForm, model: event.target.value })} /></label>
                  <label>Base URL<input value={providerForm.base_url} onChange={event => setProviderForm({ ...providerForm, base_url: event.target.value })} placeholder="https://api.example.com/v1" /></label>
                  <label>API Key<input type="password" value={providerForm.secret} onChange={event => setProviderForm({ ...providerForm, secret: event.target.value })} autoComplete="new-password" /></label>
                  <label>环境变量<input value={providerForm.api_key_env} onChange={event => setProviderForm({ ...providerForm, api_key_env: event.target.value })} placeholder="MODEL_API_KEY" /></label>
                </div>
                <button className={css.primaryButton} type="submit">保存 Provider</button>
              </form>
            </div>
          )}

          {(tab === 'plugins' || tab === 'skills') && (
            <div className={css.settingsSection}>
              <label className={css.searchBox}><Search size={16} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索名称或 ID" /></label>
              <div className={css.extensionList}>
                {filtered.map(item => (
                  <article key={item.capability_id} className={css.extensionCard}>
                    <div>
                      <span className={css.extensionIcon}>{item.kind === 'skill' ? <Sparkles size={17} /> : <Plug size={17} />}</span>
                      <div><strong>{item.public_name}</strong><code>{item.capability_id}</code></div>
                    </div>
                    <div className={css.extensionMeta}>
                      <span data-state={item.activation}>{item.activation}</span>
                      <button onClick={() => void run(() => api(`/api/v1/workspaces/${workspace.id}/extensions/${encodeURIComponent(item.capability_id)}`, json('PATCH', { enabled: !item.enabled })), item.enabled ? '已停用' : '已启用')}>{item.enabled ? '停用' : '启用'}</button>
                      {!item.trusted && <button onClick={() => void run(() => api(`/api/v1/workspaces/${workspace.id}/extensions/${encodeURIComponent(item.capability_id)}/trust`, json('POST', { trusted: true })), '已信任')}>信任</button>}
                    </div>
                    {item.diagnostics?.map((diagnostic, index) => <p className={css.diagnostic} key={index}><CircleAlert size={13} />{diagnostic.message}</p>)}
                  </article>
                ))}
              </div>
              {tab === 'plugins' ? (
                <form className={css.inlineForm} onSubmit={event => { event.preventDefault(); void run(() => api(`/api/v1/workspaces/${workspace.id}/plugins/install`, json('POST', { path: pluginPath, enable: true })), '插件已安装'); setPluginPath('') }}>
                  <input value={pluginPath} onChange={event => setPluginPath(event.target.value)} placeholder="本地插件目录" />
                  <button type="submit"><FolderPlus size={15} />安装</button>
                </form>
              ) : (
                <>
                  <div className={css.rootList}>
                    {bootstrap.skill_roots?.map(path => (
                      <div key={path}>
                        <code>{path}</code>
                        <button className={css.danger} aria-label={`移除 ${path}`} onClick={() => void run(() => api(`/api/v1/workspaces/${workspace.id}/skills/roots?path=${encodeURIComponent(path)}`, json('DELETE')), 'Skill 目录已移除')}><Trash2 size={13} /></button>
                      </div>
                    ))}
                  </div>
                  <form className={css.inlineForm} onSubmit={event => { event.preventDefault(); void run(() => api(`/api/v1/workspaces/${workspace.id}/skills/roots`, json('POST', { path: skillRoot })), 'Skill 目录已添加'); setSkillRoot('') }}>
                    <input value={skillRoot} onChange={event => setSkillRoot(event.target.value)} placeholder="Skill 根目录" />
                    <button type="submit"><FolderPlus size={15} />添加目录</button>
                  </form>
                </>
              )}
            </div>
          )}

          {tab === 'mcp' && (
            <div className={css.settingsSection}>
              <div className={css.extensionList}>
                {Object.entries(mcp.servers).map(([id, server]) => (
                  <article className={css.extensionCard} key={id}>
                    <div>
                      <span className={css.extensionIcon}><Terminal size={17} /></span>
                      <div><strong>{id}</strong><code>{String(server.transport)}</code></div>
                    </div>
                    <div className={css.extensionMeta}>
                      <span data-state={mcp.states[id]}>{mcp.states[id] ?? 'discovered'}</span>
                      <button onClick={() => void run(() => api(`/api/v1/workspaces/${workspace.id}/mcp/${id}/probe`, json('POST')), '连接测试成功')}><Play size={13} />测试</button>
                      <button className={css.danger} onClick={() => void run(() => api(`/api/v1/workspaces/${workspace.id}/mcp/${id}`, json('DELETE')), 'MCP Server 已删除')}><Trash2 size={13} /></button>
                    </div>
                  </article>
                ))}
              </div>
              <form className={css.editorForm} onSubmit={event => { event.preventDefault(); void saveMcp() }}>
                <h3><Plus size={16} />添加 MCP Server</h3>
                <div className={css.formGrid}>
                  <label>Server ID<input required value={mcpForm.id} onChange={event => setMcpForm({ ...mcpForm, id: event.target.value })} /></label>
                  <label>Transport<select value={mcpForm.transport} onChange={event => setMcpForm({ ...mcpForm, transport: event.target.value })}><option value="stdio">stdio</option><option value="streamable_http">Streamable HTTP</option></select></label>
                  {mcpForm.transport === 'stdio' ? (
                    <>
                      <label>命令<input required value={mcpForm.command} onChange={event => setMcpForm({ ...mcpForm, command: event.target.value })} /></label>
                      <label>参数<input value={mcpForm.args} onChange={event => setMcpForm({ ...mcpForm, args: event.target.value })} /></label>
                    </>
                  ) : (
                    <label className={css.wideField}>URL<input required value={mcpForm.url} onChange={event => setMcpForm({ ...mcpForm, url: event.target.value })} /></label>
                  )}
                  <label className={css.checkbox}><input type="checkbox" checked={mcpForm.required} onChange={event => setMcpForm({ ...mcpForm, required: event.target.checked })} />启动时必须连接</label>
                </div>
                <button className={css.primaryButton} type="submit">保存 MCP Server</button>
              </form>
            </div>
          )}

          {tab === 'general' && (
            <div className={css.settingsSection}>
              <article className={css.preferenceRow}>
                <div><strong>主题</strong><span>选择界面的明暗模式</span></div>
                <div className={css.segmented}>
                  {([['system', Gauge, '跟随系统'], ['light', Sun, '浅色'], ['dark', Moon, '深色']] as const).map(([id, Icon, label]) => (
                    <button key={id} data-active={theme === id || undefined} onClick={() => setTheme(id)}><Icon size={14} />{label}</button>
                  ))}
                </div>
              </article>
              <article className={css.preferenceRow}>
                <div><strong>语言</strong><span>Windcode Web 默认显示中文</span></div>
                <button><Languages size={15} />中文</button>
              </article>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
