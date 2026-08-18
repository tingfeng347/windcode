export interface Workspace {
  id: string
  name: string
  path: string
}

export interface SlashCommand {
  name: string
  description: string
  target: string | null
}

export interface Provider {
  alias: string
  provider: {
    protocol: string
    model: string
    provider_id?: string
    api_key_env?: string
    credential_id?: string
    base_url?: string
  }
  status: 'ready' | 'disconnected' | 'error'
  is_default: boolean
  credential_source?: string
  diagnostic?: string
}

export interface Bootstrap {
  version: string
  workspace: Workspace
  workspaces: Workspace[]
  permission_mode: PermissionMode
  providers: Provider[]
  primary_provider?: string
  fallback_chain: string[]
  model_ready: boolean
  mcp_status: { total: number; loaded: number; failed_servers: string[]; lazy: number }
  skill_roots?: string[]
}

export interface SessionMeta {
  session_id: string
  created_at: string
  updated_at: string
  summary: string
  status: string
}

export interface SessionRecord {
  sequence: number
  record_id: string
  record_type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface ExtensionRecord {
  capability_id: string
  public_name: string
  kind: 'skill' | 'plugin' | 'mcp_server' | string
  enabled: boolean
  trusted: boolean
  required: boolean
  activation: string
  shadowed_by?: string
  source: { scope: string; path?: string; plugin_id?: string; source_id?: string }
  diagnostics: Array<{ severity: string; message: string; suggestion?: string }>
}

export interface EventEnvelope {
  stream_sequence: number
  type: 'run.event' | 'run.finished' | 'error' | string
  workspace_id: string
  session_id?: string
  run_id?: string
  sdk_sequence?: number
  payload: Record<string, unknown>
}

export type PermissionMode = 'plan' | 'default' | 'accept_edits' | 'full_access'

export type TranscriptItem =
  | { id: string; type: 'user'; text: string }
  | { id: string; type: 'assistant'; text: string; streaming?: boolean }
  | { id: string; type: 'reasoning'; text: string }
  | { id: string; type: 'tool'; name: string; callId: string; status: string; detail: unknown }
  | { id: string; type: 'approval'; requestId: string; summary: string; choices: string[]; risk: string }
  | { id: string; type: 'question'; requestId: string; questions: Array<Record<string, unknown>> }
  | { id: string; type: 'notice'; text: string; tone?: 'error' | 'info' }
