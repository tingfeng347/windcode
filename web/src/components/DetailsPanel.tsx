import { Wrench, X } from 'lucide-react'
import { IconButton } from './IconButton'
import type { TranscriptItem } from '../types'
import css from './DetailsPanel.module.css'

interface DetailsPanelProps {
  detail: TranscriptItem | null
  onClose: () => void
}

export function DetailsPanel({ detail, onClose }: DetailsPanelProps) {
  return (
    <aside className={css.details} aria-hidden={!detail}>
      <header>
        <div><Wrench size={17} /><strong>运行详情</strong></div>
        <IconButton label="关闭详情" onClick={onClose}><X size={17} /></IconButton>
      </header>
      {detail?.type === 'tool' && (
        <div className={css.detailBody}>
          <div className={css.detailTitle}>
            <span>{detail.name}</span>
            <i data-state={detail.status}>{detail.status}</i>
          </div>
          <pre>{JSON.stringify(detail.detail, null, 2)}</pre>
        </div>
      )}
    </aside>
  )
}
