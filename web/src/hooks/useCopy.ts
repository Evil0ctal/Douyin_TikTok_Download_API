import { useCallback, useEffect, useRef, useState } from 'react'

import { copyText } from '@/lib/clipboard'

export interface UseCopyResult {
  copied: boolean
  failed: boolean
  copy: (text: string) => Promise<void>
}

/** Copy-to-clipboard with a short confirmation state. Ids are copied constantly here. */
export function useCopy(resetAfterMs = 1_600): UseCopyResult {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current)
    },
    [],
  )

  const copy = useCallback(
    async (text: string) => {
      const ok = await copyText(text)
      setCopied(ok)
      setFailed(!ok)
      if (timer.current) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => {
        setCopied(false)
        setFailed(false)
      }, resetAfterMs)
    },
    [resetAfterMs],
  )

  return { copied, failed, copy }
}
