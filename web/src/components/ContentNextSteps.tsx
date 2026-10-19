import { useTranslation } from 'react-i18next'
import { useLocation } from 'wouter'

import { Button } from './Button'
import { DownloadIcon } from './Icons'
import { useToast } from './Toast'
import { useApiMutation, useSession } from '@/hooks'
import { apiPost } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { canOpen } from '@/lib/nav'
import { atLeast } from '@/lib/roles'
import type { Platform } from '@/lib/types'

export interface PostTarget {
  platform: Platform
  contentId: string
}

export interface AuthorTarget {
  platform: Platform
  /** The archive's author key - `Author.uid`, not the handle and not `sec_uid`. */
  uid: string
}

export interface ContentNextStepsProps {
  post?: PostTarget | null
  author?: AuthorTarget | null
}

interface DownloadStarted {
  download_id: string
  archived?: boolean
  reused?: string | null
}

/**
 * Where a result goes next.
 *
 * A successful read used to end in JSON. The two things people actually do
 * with a post they have just looked up - keep its media, or find it again among
 * what this instance has archived - each lived on another page that had to be
 * found and fed the same id by hand. One component for the Playground and the
 * link parser, so the two send the same download and land on the same pages.
 *
 * The download is the body Downloads sends with its defaults, so "already
 * here" means the same thing from both. Hidden below viewer, like
 * TranscribeButton: the demo account may open Downloads but `POST /downloads`
 * is not on its write allowlist, so the button could only ever fail for it.
 */
export function ContentNextSteps({ post, author }: ContentNextStepsProps) {
  const { t } = useTranslation(['console', 'common'])
  const toast = useToast()
  const [, navigate] = useLocation()
  const session = useSession()
  const role = session.data?.role ?? null

  const download = useApiMutation<DownloadStarted, PostTarget>(
    (target) =>
      apiPost(
        paths.downloads.create,
        { platform: target.platform, content_id: target.contentId, skip_existing: true },
        { awaitTask: false },
      ),
    {
      onSuccess: (result) => {
        const open = canOpen(role, '/downloads') ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              navigate(`/downloads?id=${encodeURIComponent(result.download_id)}`)
            }}
          >
            {t('playground.next.openDownload')}
          </Button>
        ) : undefined
        toast.success(
          result.reused
            ? t('playground.next.downloadReused', { reason: result.reused })
            : result.archived === false
              ? t('playground.next.downloadFetchingFirst')
              : t('playground.next.downloadStarted'),
          { action: open },
        )
      },
      onError: (error) => {
        toast.apiError(error, t('playground.next.downloadFailed'))
      },
    },
  )

  const mayDownload = post !== null && post !== undefined && atLeast(role, 'viewer')
  const libraryOpen = canOpen(role, '/library')
  const postLink =
    post && libraryOpen
      ? `/library?platform=${encodeURIComponent(post.platform)}&content_id=${encodeURIComponent(post.contentId)}`
      : null
  const authorLink =
    author && libraryOpen ? `/library?author=${encodeURIComponent(author.uid)}` : null

  if (!mayDownload && !postLink && !authorLink) return null

  return (
    <div className="u-row u-wrap" role="group" aria-label={t('playground.next.title')}>
      <span className="u-xs u-muted">{t('playground.next.title')}</span>
      {mayDownload ? (
        <Button
          size="sm"
          variant="secondary"
          icon={<DownloadIcon size={12} />}
          loading={download.isPending}
          onClick={() => {
            if (post) download.mutate(post)
          }}
        >
          {t('playground.next.download')}
        </Button>
      ) : null}
      {postLink ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            navigate(postLink)
          }}
        >
          {t('playground.next.openPost')}
        </Button>
      ) : null}
      {authorLink ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            navigate(authorLink)
          }}
        >
          {t('playground.next.openAuthor')}
        </Button>
      ) : null}
    </div>
  )
}
