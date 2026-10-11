import { RefObject, useEffect } from 'react'
import { DisplayOpportunity, trackImpression } from './impressionTracker'

export function useAdImpression(ref: RefObject<HTMLElement>, opportunity: DisplayOpportunity, failed: () => void) {
  const { impression_id, impression_token, expires_at } = opportunity
  useEffect(() => {
    if (!ref.current) return
    return trackImpression(ref.current, { impression_id, impression_token, expires_at }, failed)
  }, [ref, impression_id, impression_token, expires_at, failed])
}
