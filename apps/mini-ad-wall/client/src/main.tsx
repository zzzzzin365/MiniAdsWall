import React, { lazy, Suspense } from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './App.css'
const MarketingConsumer = lazy(() => import('./components/MarketingConsumer'))
const campaignId = new URLSearchParams(window.location.search).get('campaign')

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {campaignId ? <Suspense fallback={<p role="status">正在加载…</p>}><MarketingConsumer campaignId={campaignId} /></Suspense> : <App />}
  </React.StrictMode>,
)
