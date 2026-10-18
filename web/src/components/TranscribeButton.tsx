import { useTranslation } from 'react-i18next'
import { useLocation } from 'wouter'

import { Button } from './Button'
import { useToast } from './Toast'
import { useApiMutation, useSession } from '@/hooks'
import { apiPost } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { atLeast } from '@/lib/roles'

export interface TranscribeButtonProps {
  /** The download holding the video, or null when there is none yet. */
  downloadId: string | null
  /** Why it cannot be transcribed now, shown under the button; null when it can. */
  unavailable?: string | null
}

/**
 * Start, or reuse, the transcription of a stored video, then open it.
 *
 * One component for the Downloads drawer and the Library drawer, so the two
 * send the same request and land on the same page. The API reuses a finished
 * transcription of the same file unless asked to refresh, so a second click
 * opens the first rather than running the model again.
 *
 * Hidden below viewer. The demo account may open both pages, but
 * `POST /transcriptions` is not on its write allowlist, so the button could
 * only ever fail for it - and a success would land it on /transcriptions,
 * which it cannot open either.
 */
export function TranscribeButton({ downloadId, unavailable = null }: TranscribeButtonProps) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const [, navigate] = useLocation()
  const session = useSession()

  const transcribe = useApiMutation<{ transcription_id: string }, string>(
    (id) =>
      apiPost(
        paths.transcriptions.create,
        { download_id: id, normalize: true, refresh: false },
        { awaitTask: false },
      ),
    {
      onSuccess: (result) => {
        navigate(`/transcriptions?id=${encodeURIComponent(result.transcription_id)}`)
      },
      onError: (error) => toast.apiError(error, t('downloads.transcription.failed')),
    },
  )

  if (!atLeast(session.data?.role ?? null, 'viewer')) return null

  return (
    <div>
      <Button
        variant="primary"
        loading={transcribe.isPending}
        disabled={downloadId === null || unavailable !== null || transcribe.isPending}
        title={unavailable ?? undefined}
        onClick={() => {
          if (downloadId !== null) transcribe.mutate(downloadId)
        }}
      >
        {t('downloads.transcription.action')}
      </Button>
      {unavailable ? <p className="u-xs u-muted">{unavailable}</p> : null}
    </div>
  )
}
