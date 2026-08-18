import { describe, expect, it } from 'vitest'
import { applyEnvelope, recordsToTranscript } from './transcript'

describe('transcript projection', () => {
  it('loads durable conversation messages', () => {
    const items = recordsToTranscript([{
      sequence: 1, record_id: 'one', record_type: 'conversation_message', created_at: '',
      payload: { role: 'user', content: [{ type: 'text', text: '你好' }] },
    }])
    expect(items).toEqual([{ id: 'one', type: 'user', text: '你好' }])
  })

  it('coalesces streaming assistant deltas by run', () => {
    const base = { stream_sequence: 1, type: 'run.event', workspace_id: 'w', run_id: 'r', payload: { kind: 'text_delta', text: 'A' } }
    const first = applyEnvelope([], base)
    const second = applyEnvelope(first, { ...base, stream_sequence: 2, payload: { kind: 'text_delta', text: 'B' } })
    expect(second).toEqual([{ id: 'assistant-r-0', type: 'assistant', text: 'AB', streaming: true }])
  })

  it('coalesces reasoning status chunks by run', () => {
    const base = { stream_sequence: 1, type: 'run.event', workspace_id: 'w', run_id: 'r', payload: { kind: 'reasoning_status', status: '正在' } }
    const first = applyEnvelope([], base)
    const second = applyEnvelope(first, { ...base, stream_sequence: 2, payload: { kind: 'reasoning_status', status: '分析' } })

    expect(second).toEqual([{ id: 'reasoning-r-0', type: 'reasoning', text: '正在分析' }])
  })

  it('places reasoning before assistant output when it arrives first', () => {
    const reasoning = applyEnvelope([], {
      stream_sequence: 1, type: 'run.event', workspace_id: 'w', run_id: 'r',
      payload: { kind: 'reasoning_status', status: '正在分析' },
    })
    const assistant = applyEnvelope(reasoning, {
      stream_sequence: 2, type: 'run.event', workspace_id: 'w', run_id: 'r',
      payload: { kind: 'text_delta', text: '回答' },
    })

    expect(assistant.map(item => item.type)).toEqual(['reasoning', 'assistant'])
  })

  it('moves late reasoning before its assistant output', () => {
    const assistant = applyEnvelope([], {
      stream_sequence: 1, type: 'run.event', workspace_id: 'w', run_id: 'r',
      payload: { kind: 'text_delta', text: '回答' },
    })
    const reasoning = applyEnvelope(assistant, {
      stream_sequence: 2, type: 'run.event', workspace_id: 'w', run_id: 'r',
      payload: { kind: 'reasoning_status', status: '正在分析' },
    })

    expect(reasoning.map(item => item.type)).toEqual(['reasoning', 'assistant'])
  })

  it('keeps model turns ordered around tool calls', () => {
    const event = (streamSequence: number, payload: Record<string, unknown>) => ({
      stream_sequence: streamSequence, type: 'run.event', workspace_id: 'w', run_id: 'r', payload,
    })
    let items = applyEnvelope([], event(1, { kind: 'reasoning_status', turn: 1, status: '先找工具' }))
    items = applyEnvelope(items, event(2, { kind: 'text_delta', turn: 1, text: '我先查询。' }))
    items = applyEnvelope(items, event(3, { kind: 'tool_started', turn: 1, event_id: 'tool', call_id: 'call', tool_name: 'search' }))
    items = applyEnvelope(items, event(4, { kind: 'tool_finished', turn: 1, call_id: 'call', result: { output: 'result' } }))
    items = applyEnvelope(items, event(5, { kind: 'reasoning_status', turn: 2, status: '整理结果' }))
    items = applyEnvelope(items, event(6, { kind: 'text_delta', turn: 2, text: '查询结果如下。' }))

    expect(items.map(item => `${item.type}:${'text' in item ? item.text : 'name' in item ? item.name : ''}`)).toEqual([
      'reasoning:先找工具',
      'assistant:我先查询。',
      'tool:search',
      'reasoning:整理结果',
      'assistant:查询结果如下。',
    ])
  })

  it('settles streaming assistant blocks when a run finishes', () => {
    const running = applyEnvelope([], {
      stream_sequence: 1, type: 'run.event', workspace_id: 'w', run_id: 'r',
      payload: { kind: 'text_delta', turn: 1, text: '完成' },
    })
    const finished = applyEnvelope(running, {
      stream_sequence: 2, type: 'run.finished', workspace_id: 'w', run_id: 'r', payload: {},
    })

    expect(finished[0]).toMatchObject({ type: 'assistant', streaming: false })
  })

  it('projects approvals and completes matching tool calls', () => {
    const tool = applyEnvelope([], {
      stream_sequence: 1,
      type: 'run.event',
      workspace_id: 'w',
      run_id: 'r',
      payload: { kind: 'tool_started', event_id: 'tool', call_id: 'call', tool_name: 'shell', arguments: { command: 'pwd' } },
    })
    const completed = applyEnvelope(tool, {
      stream_sequence: 2,
      type: 'run.event',
      workspace_id: 'w',
      run_id: 'r',
      payload: { kind: 'tool_finished', call_id: 'call', result: { output: '/tmp', is_error: false } },
    })
    const approval = applyEnvelope(completed, {
      stream_sequence: 3,
      type: 'run.event',
      workspace_id: 'w',
      run_id: 'r',
      payload: { kind: 'approval_requested', event_id: 'approval', request_id: 'request', summary: '执行命令', risk: 'medium', choices: ['allow_once', 'deny'] },
    })

    expect(completed[0]).toMatchObject({ type: 'tool', status: 'done' })
    expect(approval[1]).toMatchObject({ type: 'approval', requestId: 'request' })
  })
})
