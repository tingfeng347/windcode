import { useState } from 'react'
import { Folder } from 'lucide-react'
import { DirectoryBrowser } from './DirectoryBrowser'
import css from './EmptyWorkspace.module.css'

export function EmptyWorkspace({ onAdd }: { onAdd: (path: string) => Promise<void> }) {
  const [path, setPath] = useState('')
  const [browserOpen, setBrowserOpen] = useState(false)
  const [error, setError] = useState('')

  const add = async () => {
    try {
      setError('')
      await onAdd(path)
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    }
  }

  return (
    <section className={css.emptyWorkspace} aria-label="添加工作区">
      <img src="/windcode-neon-wind-core.svg" alt="Windcode" />
      <h1>选择工作区</h1>
      <p>添加本机项目目录后即可创建会话。</p>
      <form onSubmit={event => { event.preventDefault(); void add() }}>
        <Folder size={18} />
        <input value={path} onChange={event => setPath(event.target.value)} placeholder="/path/to/project" aria-label="工作区路径" />
        <button type="button" className={css.browseButton} onClick={() => setBrowserOpen(true)}>浏览目录</button>
        <button type="submit" disabled={!path.trim()}>添加</button>
      </form>
      {error && !browserOpen && <p className={css.formError} role="alert">{error}</p>}
      {browserOpen && (
        <DirectoryBrowser
          onClose={() => setBrowserOpen(false)}
          onSelect={selected => { setPath(selected); setBrowserOpen(false) }}
        />
      )}
    </section>
  )
}
