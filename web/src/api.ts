const JSON_HEADERS = { 'content-type': 'application/json' }

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function json(method: string, body?: unknown): RequestInit {
  return { method, headers: JSON_HEADERS, body: body === undefined ? undefined : JSON.stringify(body) }
}
