import { useCallback, useEffect, useRef, useState } from 'react'
import { DisplayOpportunity } from '../hooks/impressionTracker'
import { useAdImpression } from '../hooks/useAdImpression'
import './MarketingConsumer.css'

type Opportunity = DisplayOpportunity & { click_url: string; asset_url: string; environment: string; creative: { id: string; version: number; title: string; asset_ref: string } }
function Material({ opportunity }: { opportunity: Opportunity }) {
  const ref = useRef<HTMLElement>(null)
  const [error, setError] = useState(false)
  const failed = useCallback(() => setError(true), [])
  useAdImpression(ref, opportunity, failed)
  const video = /\.(mp4|webm|mov)(\?|$)/i.test(opportunity.creative.asset_ref)
  const asset = opportunity.asset_url
  return <article ref={ref} className="marketing-material">
    <h1>{opportunity.creative.title}</h1>
    {video ? <video src={asset} controls playsInline preload="metadata" /> : <img src={asset} alt={opportunity.creative.title} />}
    <a href={opportunity.click_url} referrerPolicy="no-referrer">查看商品</a>
    {error ? <p role="status">展示记录未送达或已过期，请刷新后重试。</p> : null}
  </article>
}
export default function MarketingConsumer({ campaignId }: { campaignId: string }) {
  const [opportunity, setOpportunity] = useState<Opportunity | null>(null)
  const [message, setMessage] = useState('正在加载…')
  useEffect(() => {
    const abort = new AbortController()
    setOpportunity(null); setMessage('正在加载…')
    fetch('/api/marketing/serve', { method: 'POST', credentials: 'same-origin', signal: abort.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ campaign_id: campaignId }) })
      .then(async response => { const body = await response.json(); if (!response.ok) throw new Error(body.error?.message || '暂时无法展示'); return body as Opportunity })
      .then(setOpportunity)
      .catch(error => { if (!abort.signal.aborted) setMessage(error.message || '暂时无法展示') })
    return () => abort.abort()
  }, [campaignId])
  return <main className="marketing-consumer">
    <p>{opportunity?.environment === 'production' ? '品牌精选' : '营销演示 · 模拟环境'}</p>
    {opportunity ? <Material key={opportunity.impression_id} opportunity={opportunity} /> : <p role="status">{message}</p>}
  </main>
}
