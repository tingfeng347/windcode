import { useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Bot, ChevronRight, CircleAlert, Code2, Shield, Sparkles, Terminal, Wrench } from 'lucide-react'
import { EmptyWorkspace } from './EmptyWorkspace'
import type { TranscriptItem } from '../types'
import css from './Chat.module.css'

type ApprovalItem = Extract<TranscriptItem, { type: 'approval' }>
type QuestionItem = Extract<TranscriptItem, { type: 'question' }>

interface ChatProps {
  hasWorkspace: boolean
  items: TranscriptItem[]
  activeRun: string | null
  onAddWorkspace: (path: string) => Promise<void>
  onApproval: (item: ApprovalItem, decision: string) => void
  onQuestions: (item: QuestionItem, form: HTMLFormElement) => void
  onOpenDetail: (item: TranscriptItem) => void
  onSuggestion: (prompt: string) => void
}

const suggestions: Array<[string, typeof Code2, string]> = [
  ['分析这个项目的结构并给出改进建议', Code2, '分析项目'],
  ['检查当前 Git 变更并指出风险', Shield, '检查变更'],
  ['运行测试并修复失败项', Wrench, '运行测试'],
]

export function Chat({
  hasWorkspace, items, activeRun, onAddWorkspace, onApproval, onQuestions, onOpenDetail, onSuggestion,
}: ChatProps) {
  const chatEnd = useRef<HTMLDivElement | null>(null)
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: 'end' }) }, [items])

  return (
    <section className={css.chat} data-conversation-scroll>
      {!hasWorkspace ? (
        <EmptyWorkspace onAdd={onAddWorkspace} />
      ) : (
        <>
          {items.length === 0 && (
            <div className={css.hero}>
              <img src="/windcode-neon-wind-core.svg" alt="Windcode" />
              <h1>我能帮你处理什么？</h1>
              <p>在当前工作区中阅读代码、修改文件、执行命令并验证结果。</p>
              <div>
                {suggestions.map(([prompt, Icon, label]) => (
                  <button key={label} onClick={() => onSuggestion(prompt)}><Icon size={16} />{label}</button>
                ))}
              </div>
            </div>
          )}
          {items.map(item => {
            if (item.type === 'user') return <article className={css.userMessage} key={item.id}>{item.text}</article>
            if (item.type === 'assistant') return (
              <article className={css.assistantMessage} key={item.id}>
                <div className={css.assistantIcon}><Bot size={16} /></div>
                <div className={css.markdown}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
                  {item.streaming && activeRun && <span className={css.cursor} />}
                </div>
              </article>
            )
            if (item.type === 'reasoning') return (
              <div className={css.compactRow} key={item.id}><Sparkles size={15} /><span>推理</span><i>{item.text}</i></div>
            )
            if (item.type === 'tool') return (
              <button className={css.toolRow} key={item.id} data-state={item.status} onClick={() => onOpenDetail(item)}>
                <Terminal size={15} /><span>{item.name}</span>
                <i>{item.status === 'running' ? '执行中' : item.status === 'error' ? '失败' : '完成'}</i>
                <ChevronRight size={14} />
              </button>
            )
            if (item.type === 'approval') return (
              <article className={css.approval} key={item.id}>
                <div>
                  <Shield size={18} />
                  <div>
                    <strong>需要授权</strong>
                    <p>{item.summary}</p>
                    <span>风险：{item.risk}</span>
                  </div>
                </div>
                <div>
                  {item.choices.map(choice => (
                    <button key={choice} data-primary={choice.includes('allow') || undefined} onClick={() => onApproval(item, choice)}>{choice}</button>
                  ))}
                </div>
              </article>
            )
            if (item.type === 'question') return (
              <form className={css.question} key={item.id} onSubmit={event => { event.preventDefault(); onQuestions(item, event.currentTarget) }}>
                <strong>需要你的输入</strong>
                {item.questions.map((question, index) => {
                  const id = String(question.id ?? `question_${index}`)
                  return (
                    <label key={id}>
                      {String(question.question ?? question.prompt ?? `问题 ${index + 1}`)}
                      <input name={id} required />
                    </label>
                  )
                })}
                <button type="submit">提交回答</button>
              </form>
            )
            return <div className={css.notice} data-tone={item.tone} key={item.id}><CircleAlert size={15} />{item.text}</div>
          })}
          <div ref={chatEnd} />
        </>
      )}
    </section>
  )
}
