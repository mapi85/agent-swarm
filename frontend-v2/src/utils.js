// Formatage + rendu Markdown minimal et échappé (le contenu vient des LLM/agents).

export function fmtTokens(n) {
  n = n || 0
  if (n >= 1e6) return (n / 1e6).toFixed(1) + ' M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + ' k'
  return String(n)
}

export function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso.includes('Z') || iso.includes('+') ? iso : iso + 'Z')
  return d.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ))
}

// Markdown -> HTML sûr : on échappe d'abord tout, puis on applique un sous-ensemble.
export function md(src) {
  if (!src) return ''
  let s = esc(src)
  // blocs de code
  s = s.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code.replace(/^\n/, '')}</code></pre>`)
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>')
  // titres
  s = s.replace(/^######\s+(.*)$/gm, '<h6>$1</h6>')
    .replace(/^#####\s+(.*)$/gm, '<h5>$1</h5>')
    .replace(/^####\s+(.*)$/gm, '<h4>$1</h4>')
    .replace(/^###\s+(.*)$/gm, '<h3>$1</h3>')
    .replace(/^##\s+(.*)$/gm, '<h2>$1</h2>')
    .replace(/^#\s+(.*)$/gm, '<h1>$1</h1>')
  // gras / italique
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  s = s.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
  // liens http(s) seulement
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
  // listes
  s = s.replace(/^(?:- |\* )(.*)$/gm, '<li>$1</li>')
  s = s.replace(/(<li>[\s\S]*?<\/li>)/g, (m) => `<ul>${m}</ul>`)
  // paragraphes / sauts de ligne
  s = s.replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>')
  return `<p>${s}</p>`.replace(/<p>(<(?:h\d|ul|pre)>)/g, '$1').replace(/(<\/(?:h\d|ul|pre)>)<\/p>/g, '$1')
}

export const TASK_STATUS = {
  pending: { label: 'En attente', cls: 'gray' },
  ready: { label: 'Prête', cls: 'blue' },
  in_progress: { label: 'En cours', cls: 'blue' },
  waiting_user: { label: 'Attend une réponse', cls: 'violet' },
  done: { label: 'Terminée', cls: 'green' },
  failed: { label: 'Échec', cls: 'red' },
  cancelled: { label: 'Annulée', cls: 'gray' },
}

export const SESSION_STATUS = {
  planned: { label: 'Planifiée', cls: 'violet' },
  running: { label: 'En cours', cls: 'blue' },
  completed: { label: 'Terminée', cls: 'green' },
  failed: { label: 'Échec', cls: 'red' },
  interrupted: { label: 'Interrompue', cls: 'amber' },
}

export const MISSION_STATUS = {
  proposed: { label: 'Proposée', cls: 'blue' },
  running: { label: 'En cours', cls: 'blue' },
  completed: { label: 'Terminée', cls: 'green' },
  needs_attention: { label: 'À surveiller', cls: 'amber' },
  archived: { label: 'Archivée', cls: 'gray' },
}
