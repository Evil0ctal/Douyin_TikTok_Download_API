import { useTranslation } from 'react-i18next'
import { useLocation } from 'wouter'

import { Button } from './Button'
import { useToast } from './Toast'
import { useApiMutation, useApiQuery, useSession } from '@/hooks'
import { apiPost } from '@/lib/api'
import { transcriptHref } from '@/lib/contentLinks'
import { paths } from '@/lib/endpoints'
import { atLeast } from '@/lib/roles'

/** A transcription a listing says already exists for the download. */
export interface ExistingTranscription {
  transcription_id: string
  state: string
}

export interface TranscribeButtonProps {
  /** The download holding the video, or null when there is none yet. */
  downloadId: string | null
  /** Why it cannot be transcribed now, shown under the button; null when it can. */
  unavailable?: string | null
  /**
   * The transcript this download already has, when the listing said so. The
   * button opens it rather than offering to start one: the API reuses a
   * finished transcript unless asked to refresh, so a click could only ever
   * be handed back this record.
   */
  existing?: ExistingTranscription | null
}

/**
 * Whether this instance can transcribe at all, from `components.transcriber`.
 *
 * `off` is the profile not being enabled, which is the default - the button
 * was always enabled and every click answered TRANSCRIBER_UNAVAILABLE. `down`
 * is configured and not answering. `unknown` covers a server that does not
 * report the component (older builds) and a status read that failed; nothing
 * is blocked on it, so the button behaves as it always did.
 */
export type TranscriberState = 'ready' | 'off' | 'down' | 'unknown'

interface StatusComponents {
  components?: Record<string, { configured?: boolean; ok?: boolean | null } | undefined>
}

export function useTranscriberState(): TranscriberState {
  // Read when it is needed rather than polled: the status call probes every
  // component, and whether a compose profile is on changes by redeploying,
  // not while a drawer is open.
  const status = useApiQuery<StatusComponents>({
    key: ['system', 'status'],
    path: paths.system.status,
    retry: false,
  })
  const component = status.data?.components?.['transcriber']
  if (!component || typeof component.configured !== 'boolean') return 'unknown'
  if (!component.configured) return 'off'
  if (component.ok === false) return 'down'
  return component.ok === true ? 'ready' : 'unknown'
}

/** The installation guide's section on the optional profiles, per language. */
const PROFILES_GUIDE = {
  en: 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/documents/en/02-installation.md#the-optional-profiles',
  // The heading is Chinese, so its anchor is too: percent-encoded here
  // because source files stay ASCII.
  zh: 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/documents/zh/02-installation.md#%E5%8F%AF%E9%80%89-profile',
} as const

export function transcriberGuideHref(language: string): string {
  return language.startsWith('zh') ? PROFILES_GUIDE.zh : PROFILES_GUIDE.en
}

/**
 * Why nothing can be transcribed, and what turns it on. Null while it can be.
 *
 * Shared by the button and by the Transcriptions page, so the two cannot give
 * different instructions for the same switch.
 */
export function TranscriberNotice({ state }: { state: TranscriberState }) {
  const { t, i18n } = useTranslation(['console', 'common'])
  if (state !== 'off' && state !== 'down') return null
  return (
    <p className="u-xs u-muted">
      {t(`transcriptions.transcriber.${state}Body`)}{' '}
      {state === 'off' ? (
        <a href={transcriberGuideHref(i18n.language)} target="_blank" rel="noreferrer noopener">
          {t('transcriptions.transcriber.guide')}
        </a>
      ) : null}
    </p>
  )
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
export function TranscribeButton({
  downloadId,
  unavailable = null,
  existing = null,
}: TranscribeButtonProps) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const [, navigate] = useLocation()
  const session = useSession()
  const transcriber = useTranscriberState()

  const transcribe = useApiMutation<{ transcription_id: string }, string>(
    (id) =>
      apiPost(
        paths.transcriptions.create,
        { download_id: id, normalize: true, refresh: false },
        { awaitTask: false },
      ),
    {
      onSuccess: (result) => {
        navigate(transcriptHref(result.transcription_id))
      },
      onError: (error) => toast.apiError(error, t('downloads.transcription.failed')),
    },
  )

  if (!atLeast(session.data?.role ?? null, 'viewer')) return null

  // Before the transcriber check: a transcript already made can be read
  // whether or not the transcriber is running now.
  if (existing) {
    return (
      <div>
        <Button
          variant="primary"
          onClick={() => {
            navigate(transcriptHref(existing.transcription_id))
          }}
        >
          {existing.state === 'done'
            ? t('downloads.transcription.open')
            : t('downloads.transcription.openRunning')}
        </Button>
      </div>
    )
  }

  const blocked = transcriber === 'off' || transcriber === 'down'
  const reason = unavailable ?? (blocked ? t(`transcriptions.transcriber.${transcriber}Title`) : null)

  return (
    <div>
      <Button
        variant="primary"
        loading={transcribe.isPending}
        disabled={downloadId === null || reason !== null || transcribe.isPending}
        title={reason ?? undefined}
        onClick={() => {
          if (downloadId !== null) transcribe.mutate(downloadId)
        }}
      >
        {t('downloads.transcription.action')}
      </Button>
      {unavailable ? (
        <p className="u-xs u-muted">{unavailable}</p>
      ) : (
        <TranscriberNotice state={transcriber} />
      )}
    </div>
  )
}
