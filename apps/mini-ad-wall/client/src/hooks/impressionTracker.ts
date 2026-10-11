export type DisplayOpportunity = { impression_id: string; impression_token: string; expires_at: string }
type Pending = { event_id: string; impression_token: string; occurred_at?: string; sent: boolean }
// Bound to this page, retained across React remounts. Server remains the deduplication authority.
const pending = new Map<string, Pending>()
export function trackImpression(element: Element, opportunity: DisplayOpportunity, failed: () => void) {
  if (!('IntersectionObserver' in window)) { failed(); return () => {} }
  if (pending.size > 200) pending.delete(pending.keys().next().value!)
  let state = pending.get(opportunity.impression_id)
  if (!state) {
    state = { event_id: crypto.randomUUID(), impression_token: opportunity.impression_token, sent: false }
    pending.set(opportunity.impression_id, state)
  }
  const event = state
  let visible = false, stopped = false, sending = false, attempts = 0
  let clock: ReturnType<typeof setTimeout> | undefined
  let retry: ReturnType<typeof setTimeout> | undefined
  const abort = new AbortController()
  const cancelClock = () => { clearTimeout(clock); clock = undefined }
  async function send() {
    if (stopped || sending || event.sent || !event.occurred_at) return
    if (Date.now() >= new Date(opportunity.expires_at).getTime()) { failed(); return }
    sending = true
    try {
      const response = await fetch('/api/marketing/events/impressions', {
        method: 'POST', credentials: 'same-origin', signal: abort.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ event_id: event.event_id, impression_token: event.impression_token, occurred_at: event.occurred_at })
      })
      if (response.ok) { event.sent = true; return }
      if (response.status < 500 && response.status !== 429) { if (!stopped) failed(); return }
      throw new Error('retryable')
    } catch {
      if (!stopped) {
        if (++attempts <= 5) retry = setTimeout(send, Math.min(1000 * 2 ** (attempts - 1), 10000))
        else failed()
      }
    } finally { sending = false }
  }
  function update() {
    if (event.sent || event.occurred_at || stopped || !visible || document.visibilityState !== 'visible') { cancelClock(); return }
    if (clock === undefined) clock = setTimeout(() => {
      clock = undefined
      if (visible && document.visibilityState === 'visible' && !stopped) { event.occurred_at = new Date().toISOString(); void send() }
    }, 1000)
  }
  const observer = new IntersectionObserver(entries => {
    visible = entries.some(entry => entry.target === element && entry.isIntersecting && entry.intersectionRatio >= 0.5)
    update()
  }, { threshold: [0, 0.5, 1] })
  observer.observe(element)
  document.addEventListener('visibilitychange', update)
  if (event.occurred_at && !event.sent) void send()
  return () => {
    stopped = true; cancelClock(); clearTimeout(retry); abort.abort()
    observer.disconnect(); document.removeEventListener('visibilitychange', update)
  }
}
