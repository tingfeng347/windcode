import type { EventEnvelope, SessionRecord, TranscriptItem } from './types'

function textFromContent(content: unknown): string {
  if (!Array.isArray(content)) return ''
  return content
    .filter(item => typeof item === 'object' && item !== null)
    .map(item => {
      const block = item as Record<string, unknown>
      if (block.type === 'text') return String(block.text ?? '')
      if (block.type === 'tool_result') return String(block.output ?? '')
      return ''
    })
    .filter(Boolean)
    .join('\n')
}

export function recordsToTranscript(records: SessionRecord[]): TranscriptItem[] {
  return records.flatMap(record => {
    if (record.record_type !== 'conversation_message') return []
    const role = String(record.payload.role ?? '')
    const text = textFromContent(record.payload.content)
    if (!text || (role !== 'user' && role !== 'assistant')) return []
    return [{ id: record.record_id, type: role, text } as TranscriptItem]
  })
}

export function applyEnvelope(items: TranscriptItem[], envelope: EventEnvelope): TranscriptItem[] {
  if (envelope.type === 'error') {
    return [...items, { id: `error-${envelope.stream_sequence}`, type: 'notice', tone: 'error', text: String(envelope.payload.message ?? '运行失败') }]
  }
  if (envelope.type === 'run.finished') {
    const prefix = `assistant-${envelope.run_id ?? ''}-`
    return items.map(item => item.type === 'assistant' && item.id.startsWith(prefix) ? { ...item, streaming: false } : item)
  }
  if (envelope.type !== 'run.event') return items
  const payload = envelope.payload
  const kind = String(payload.kind ?? '')
  const id = String(payload.event_id ?? `${kind}-${envelope.stream_sequence}`)
  const turn = String(payload.turn ?? 0)
  if (kind === 'run_started') {
    const prompt = String(payload.prompt ?? '')
    const duplicate = items.some(item => item.type === 'user' && item.text === prompt && item.id.startsWith('pending-'))
    return duplicate ? items : [...items, { id, type: 'user', text: prompt }]
  }
  if (kind === 'text_delta') {
    const runId = envelope.run_id ?? id
    const assistantId = `assistant-${runId}-${turn}`
    const index = items.findIndex(item => item.id === assistantId)
    if (index === -1) {
      const assistant: TranscriptItem = { id: assistantId, type: 'assistant', text: String(payload.text ?? ''), streaming: true }
      const settled = items.map(item => item.type === 'assistant' && item.id.startsWith(`assistant-${runId}-`) ? { ...item, streaming: false } : item)
      const reasoningIndex = settled.findIndex(item => item.id === `reasoning-${runId}-${turn}`)
      if (reasoningIndex === -1) return [...settled, assistant]
      return [...settled.slice(0, reasoningIndex + 1), assistant, ...settled.slice(reasoningIndex + 1)]
    }
    const next = [...items]
    const current = next[index]
    if (current.type === 'assistant') next[index] = { ...current, text: current.text + String(payload.text ?? '') }
    return next
  }
  if (kind === 'reasoning_status') {
    const reasoningId = `reasoning-${envelope.run_id ?? id}-${turn}`
    const index = items.findIndex(item => item.id === reasoningId)
    if (index === -1) {
      const reasoning: TranscriptItem = { id: reasoningId, type: 'reasoning', text: String(payload.status ?? '') }
      const assistantIndex = items.findIndex(item => item.id === `assistant-${envelope.run_id ?? id}-${turn}`)
      if (assistantIndex === -1) return [...items, reasoning]
      return [...items.slice(0, assistantIndex), reasoning, ...items.slice(assistantIndex)]
    }
    const next = [...items]
    const current = next[index]
    if (current.type === 'reasoning') next[index] = { ...current, text: current.text + String(payload.status ?? '') }
    return next
  }
  if (kind === 'tool_started') return [...items, { id, type: 'tool', name: String(payload.tool_name ?? 'Tool'), callId: String(payload.call_id ?? ''), status: 'running', detail: payload.arguments }]
  if (kind === 'tool_finished') {
    const callId = String(payload.call_id ?? '')
    const index = items.findIndex(item => item.type === 'tool' && item.callId === callId)
    if (index === -1) return items
    const next = [...items]
    const current = next[index]
    if (current.type === 'tool') next[index] = { ...current, status: (payload.result as Record<string, unknown>)?.is_error ? 'error' : 'done', detail: payload.result }
    return next
  }
  if (kind === 'approval_requested') return [...items, { id, type: 'approval', requestId: String(payload.request_id), summary: String(payload.summary ?? ''), risk: String(payload.risk ?? ''), choices: Array.isArray(payload.choices) ? payload.choices.map(String) : [] }]
  if (kind === 'user_input_requested') return [...items, { id, type: 'question', requestId: String(payload.request_id), questions: Array.isArray(payload.questions) ? payload.questions as Array<Record<string, unknown>> : [] }]
  if (kind === 'run_failed' || kind === 'run_cancelled') return [...items, { id, type: 'notice', tone: kind === 'run_failed' ? 'error' : 'info', text: String(payload.message ?? payload.reason ?? '运行已结束') }]
  return items
}
