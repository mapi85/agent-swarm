// Client API : fetch + Bearer, gestion 401, et consommation SSE via fetch (pas d'EventSource → pas de token en URL).
import { useAuth } from './store.js'

async function request(method, url, body) {
  const auth = useAuth()
  const headers = { 'Content-Type': 'application/json' }
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`
  const opts = { method, headers }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const res = await fetch(url, opts)
  if (res.status === 401) {
    auth.logout()
    throw new Error('Session expirée, reconnecte-toi.')
  }
  if (res.status === 204) return null
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = data && data.detail
    throw new Error(typeof detail === 'string' ? detail : (detail ? JSON.stringify(detail) : `Erreur ${res.status}`))
  }
  return data
}

export const api = {
  get: (u) => request('GET', u),
  post: (u, b) => request('POST', u, b),
  patch: (u, b) => request('PATCH', u, b),
  put: (u, b) => request('PUT', u, b),
  del: (u) => request('DELETE', u),
}

// Ouvre un flux SSE et appelle onEvent(eventName, data) par message. Renvoie une fonction d'arrêt.
export function stream(url, onEvent) {
  const auth = useAuth()
  const controller = new AbortController()
  ;(async () => {
    let res
    try {
      res = await fetch(url, {
        headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {},
        signal: controller.signal,
      })
    } catch { return }
    if (!res.ok || !res.body) return
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      let chunk
      try { chunk = await reader.read() } catch { break }
      if (chunk.done) break
      buf += decoder.decode(chunk.value, { stream: true })
      const parts = buf.split('\n\n')
      buf = parts.pop()
      for (const block of parts) {
        let ev = 'message', data = ''
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) ev = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        if (data) { try { onEvent(ev, JSON.parse(data)) } catch { /* keep-alive */ } }
      }
    }
  })()
  return () => controller.abort()
}
