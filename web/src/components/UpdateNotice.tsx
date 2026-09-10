import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'

import { useApiQuery, useSession } from '@/hooks'
import { paths } from '@/lib/endpoints'
import { isNewerRelease } from '@/lib/version'

import { Button } from './Button'
import { ExternalIcon } from './Icons'
import { Modal } from './Modal'

/**
 * "There is a newer release", once a day, after signing in.
 *
 * Three decisions worth keeping:
 *
 * **The request goes from this browser to github.com, never from the server.**
 * That is the same posture the System page's manual check has, and it is why
 * `system.check_updates` is a setting at all: an instance that phoned home on
 * its own would be reporting that this deployment exists, how many people use
 * it and how often. Here the operator's own browser asks, and only if they left
 * the setting on.
 *
 * **Once per 24 hours, per browser.** A modal on every navigation is a modal
 * people learn to dismiss without reading. The timestamp is in `localStorage`,
 * so it is per-browser rather than per-instance - which is the right grain: the
 * thing being interrupted is a person, not a server.
 *
 * **Never for the demo account.** A visitor on a public demo cannot upgrade
 * anything, and telling them to is both noise and a small leak of how the
 * operator runs their box.
 */

const RELEASES_API =
  'https://api.github.com/repos/Evil0ctal/Douyin_TikTok_Download_API/releases/latest'
const RELEASES_PAGE = 'https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases'

const SEEN_KEY = 'dtk.updateNotice.lastShown'
const DAY_MS = 24 * 60 * 60 * 1000

const CHECK_UPDATES_KEY = 'system.check_updates'

interface SystemStatus {
  version: string
}

interface SettingRow {
  key: string
  value: unknown
}

interface SettingsResponse {
  settings: SettingRow[]
}

interface Release {
  tag_name?: string
  html_url?: string
  published_at?: string
}

/** Reading localStorage throws in some privacy modes; a notice is not worth a crash. */
function shownRecently(): boolean {
  try {
    const raw = window.localStorage.getItem(SEEN_KEY)
    if (!raw) return false
    return Date.now() - Number.parseInt(raw, 10) < DAY_MS
  } catch {
    // Cannot remember, so do not nag: treat it as already shown.
    return true
  }
}

function rememberShown(): void {
  try {
    window.localStorage.setItem(SEEN_KEY, String(Date.now()))
  } catch {
    /* nothing to do; the notice simply reappears next time */
  }
}

export function UpdateNotice() {
  const { t } = useTranslation(['console', 'common'])
  const session = useSession()
  const [dismissed, setDismissed] = useState(false)

  const isDemo = session.data?.role === 'demo'
  const signedIn = Boolean(session.data) && !isDemo

  const status = useApiQuery<SystemStatus>({
    key: ['system', 'status'],
    path: paths.system.status,
    enabled: signedIn,
  })

  // The setting lives behind the admin read gate, which a viewer clears. If the
  // call fails - a role without it, a transient error - `enabled` stays false
  // and no request goes anywhere, which is the right way for this to fail.
  const settings = useApiQuery<SettingsResponse>({
    key: ['admin', 'settings'],
    path: paths.settings.list,
    enabled: signedIn,
  })

  const optedIn =
    settings.data?.settings.find((row) => row.key === CHECK_UPDATES_KEY)?.value === true

  const shouldAsk = signedIn && optedIn && !shownRecently()

  const release = useQuery<Release>({
    queryKey: ['github', 'latest-release'],
    enabled: shouldAsk,
    retry: false,
    staleTime: DAY_MS,
    queryFn: async () => {
      const response = await fetch(RELEASES_API, {
        headers: { Accept: 'application/vnd.github+json' },
      })
      if (!response.ok) throw new Error(String(response.status))
      return (await response.json()) as Release
    },
  })

  const tag = release.data?.tag_name ?? null
  const outdated = shouldAsk && isNewerRelease(tag, status.data?.version)

  useEffect(() => {
    if (outdated) rememberShown()
  }, [outdated])

  if (!outdated || dismissed) return null

  const close = () => {
    setDismissed(true)
  }

  return (
    <Modal
      open
      onClose={close}
      size="sm"
      title={t('console:updates.notice.title')}
      description={t('console:updates.notice.body', {
        latest: tag,
        current: status.data?.version ?? '',
      })}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            {t('common:action.close')}
          </Button>
          <Button
            onClick={() => {
              window.open(release.data?.html_url ?? RELEASES_PAGE, '_blank', 'noopener')
              close()
            }}
          >
            <ExternalIcon size={12} />
            {t('console:updates.notice.releaseNotes')}
          </Button>
        </>
      }
    >
      <p className="u-secondary" style={{ margin: 0 }}>
        {t('console:updates.notice.how')}
      </p>
    </Modal>
  )
}
