import { useEffect, useState } from 'react'
import { ArrowUp, Check, ChevronRight, Folder, FolderOpen, X } from 'lucide-react'
import { api } from '../api'
import css from './DirectoryBrowser.module.css'

interface DirectoryListing {
  path: string
  parent: string
  items: { name: string; path: string }[]
}

interface DirectoryBrowserProps {
  onClose: () => void
  onSelect: (path: string) => void
}

export function DirectoryBrowser({ onClose, onSelect }: DirectoryBrowserProps) {
  const [listing, setListing] = useState<DirectoryListing | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const browse = async (nextPath?: string) => {
    try {
      setLoading(true)
      setError('')
      setListing(await api<DirectoryListing>(`/api/v1/directories${nextPath ? `?path=${encodeURIComponent(nextPath)}` : ''}`))
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void browse() }, [])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className={css.directoryBackdrop} role="presentation" onClick={onClose}>
      <div
        className={css.directoryBrowser}
        role="dialog"
        aria-modal="true"
        aria-label="选择项目目录"
        onClick={event => event.stopPropagation()}
      >
        <header>
          <FolderOpen size={17} />
          <strong>选择项目目录</strong>
          <button type="button" onClick={onClose} aria-label="关闭目录浏览"><X size={17} /></button>
        </header>
        <div className={css.directoryPath}>
          <button
            type="button"
            disabled={!listing || listing.parent === listing.path}
            onClick={() => void browse(listing?.parent)}
            aria-label="上级目录"
          >
            <ArrowUp size={14} />上级
          </button>
          <code title={listing?.path}>{listing ? listing.path : loading ? '正在读取…' : ' '}</code>
          <button
            type="button"
            className={css.directoryPick}
            disabled={!listing}
            onClick={() => listing && onSelect(listing.path)}
          >
            <Check size={14} />选择此目录
          </button>
        </div>
        {error && <p className={css.directoryError} role="alert">{error}</p>}
        <nav aria-label="目录列表">
          {listing && listing.items.length === 0 && !loading && (
            <p className={css.directoryEmpty}>此目录下没有可见的子目录</p>
          )}
          {listing?.items.map(item => (
            <button type="button" key={item.path} onClick={() => void browse(item.path)}>
              <Folder size={16} />
              <span>{item.name}</span>
              <ChevronRight size={15} />
            </button>
          ))}
        </nav>
      </div>
    </div>
  )
}
