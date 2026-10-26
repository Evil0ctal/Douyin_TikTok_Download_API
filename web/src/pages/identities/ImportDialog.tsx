import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  AlertIcon,
  Banner,
  Button,
  Checkbox,
  ErrorState,
  InfoIcon,
  Input,
  MaskedSecret,
  Modal,
  Select,
  Skeleton,
  StatusBadge,
  Textarea,
  useToast,
} from '@/components'
import { useApiMutation, useApiQuery, useFormatters } from '@/hooks'
import { apiPost, type ApiError } from '@/lib/api'
import { copyText } from '@/lib/clipboard'
import { paths } from '@/lib/endpoints'
import { MISSING } from '@/lib/format'
import { PLATFORMS, type Platform } from '@/lib/types'

import styles from '../identities.module.css'
import { NOTE_MAX } from './NoteField'
import { Fact, hasLapsed, usePlatformInSentence, usePlatformName } from './shared'
import {
  POOL_KEY,
  type BundleImportResult,
  type CookieReport,
  type CookieRole,
  type IdentityBundleFile,
  type ImportResponse,
  type PoolLevel,
  type ProxyOption,
} from './types'

/**
 * What a jar from each platform looks like, as the empty box's example.
 *
 * Cookie names, which read the same in every language. It was one ByteDance
 * example for all three, so the box asked for a `ttwid` the Bilibili jar being
 * pasted into it was never going to have.
 */
const PLACEHOLDERS: Record<Platform, string> = {
  douyin: 'ttwid=...; odin_tt=...; s_v_web_id=...; sessionid=...; sid_guard=...',
  tiktok: 'ttwid=...; msToken=...; tt_chain_token=...; sessionid=...; sid_guard=...',
  bilibili: 'buvid3=...; buvid4=...; b_nut=...; SESSDATA=...; bili_jct=...',
}

const ROLE_COLOR: Record<CookieRole, string> = {
  required: 'var(--accent)',
  session: 'var(--warning)',
  useful: 'var(--text-secondary)',
  other: 'var(--text-muted)',
}

interface ImportDialogProps {
  open: boolean
  /** The platform to start on; the refill card's "Import a login" sets it. */
  preset: Platform | null
  proxies: ProxyOption[]
  onClose: () => void
  /** Refresh the page's queries. */
  onDone: () => Promise<void>
  /** One identity was stored: open it, and check its login if it has one. */
  onImported: (identityId: string, authenticated: boolean) => void
}

/**
 * Cookie import.
 *
 * Two things this dialog must do, both from docs/design/07-frontend.md: accept
 * all four paste formats without asking which one it is, and say plainly that
 * the pasted value is the account itself. V4 had users commit cookies to
 * config.yaml in git; the way not to repeat that is to make the stakes visible
 * at the moment of pasting.
 *
 * Mounted afresh for every opening (the page keys it), so a preset platform
 * takes effect and nothing from the last import - a paste, a loaded file, a
 * proxy - is waiting in it. `close` clears it all as well: the paste is a
 * credential and does not linger in component state.
 */
export function ImportDialog({
  open,
  preset,
  proxies,
  onClose,
  onDone,
  onImported,
}: ImportDialogProps) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()
  const platformName = usePlatformName()
  const platformPhrase = usePlatformInSentence()

  const pool = useApiQuery<PoolLevel>({ key: POOL_KEY, path: paths.identities.pool })

  const [platform, setPlatform] = useState<Platform>(preset ?? 'douyin')
  const [cookies, setCookies] = useState('')
  const [userAgent, setUserAgent] = useState('')
  const [proxyId, setProxyId] = useState('')
  const [reserved, setReserved] = useState(false)
  const [note, setNote] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  //: A file this build exported, once it has been read and understood. The
  //: paste path and this one cannot both be armed: they mean different things
  //: - one jar typed in, or a pool restored - and a dialog that guessed which
  //: was intended would guess wrong on the day it mattered.
  const [bundle, setBundle] = useState<{ file: string; doc: IdentityBundleFile } | null>(null)
  const [preview, setPreview] = useState<{
    loading: boolean
    report?: CookieReport
    error?: ApiError
  }>({ loading: false })

  // Live preview: the server is the authority on what the paste means, so the
  // dry run is what gets rendered. Debounced, because it runs on every
  // keystroke. Not while a file is loaded: the paste form is off then, and a
  // preview of a paste nobody can store was a second answer to a question
  // nobody was asking.
  useEffect(() => {
    const text = cookies.trim()
    if (!open || bundle !== null || text.length < 8) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- clears the debounced preview when there is nothing to dry-run
      setPreview({ loading: false })
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setPreview({ loading: true })
      apiPost<ImportResponse>(
        paths.identities.import,
        {
          platform,
          cookies: text,
          user_agent: userAgent.trim() || null,
          dry_run: true,
        },
        { signal: controller.signal },
      )
        .then((response) => {
          setPreview({ loading: false, report: response.report })
        })
        .catch((error: ApiError) => {
          if (error.kind !== 'aborted') setPreview({ loading: false, error })
        })
    }, 600)

    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [open, bundle, cookies, platform, userAgent])

  /**
   * What restoring the loaded file would do, before it does it.
   *
   * The route has always judged every entry on its own and said why it skipped
   * one; the dialog restored blind and then summed the skips up as "expired or
   * empty", which was right for one of the three reasons.
   */
  const check = useApiMutation<BundleImportResult, IdentityBundleFile>((doc) =>
    apiPost<BundleImportResult>(paths.identities.importBundle, {
      version: doc.version,
      identities: doc.identities,
      dry_run: true,
    }),
  )

  /**
   * Restore a whole document, fingerprints and all.
   *
   * Not the paste route with the jars re-serialised through it: that infers
   * the browser from a user agent no longer beside the cookies, so a restored
   * identity would sign with a different fingerprint from the one the platform
   * first saw it with - which is the difference risk control looks for.
   */
  const restore = useApiMutation<BundleImportResult, IdentityBundleFile>(
    (doc) =>
      apiPost<BundleImportResult>(paths.identities.importBundle, {
        version: doc.version,
        identities: doc.identities,
        proxy_id: proxyId || null,
        dry_run: false,
      }),
    {
      onSuccess: async (result) => {
        const skipped = result.results.filter((entry) => !entry.identity_id)
        toast.success(t('console:identity.restore.done', { count: result.stored }), {
          description:
            skipped.length > 0
              ? t('console:identity.restore.skipped', {
                  count: skipped.length,
                  reasons: skipReasons(skipped.map((entry) => entry.reason)),
                })
              : undefined,
        })
        close()
        await onDone()
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.restore.failed'))
      },
    },
  )

  /** "no cookies (2), unusable (1)", in this language. */
  function skipReasons(reasons: Array<string | undefined>): string {
    const counts = new Map<string, number>()
    for (const reason of reasons) {
      const label = t(`console:identity.restore.reason.${reason ?? 'unusable'}`, {
        defaultValue: reason ?? '',
      })
      counts.set(label, (counts.get(label) ?? 0) + 1)
    }
    return [...counts.entries()]
      .map(([label, times]) =>
        t('console:identity.restore.reasonCount', { reason: label, count: times }),
      )
      .join(', ')
  }

  /** Read a chosen file and check it is one of ours before arming the button. */
  const readBundle = async (file: File): Promise<void> => {
    try {
      const parsed: unknown = JSON.parse(await file.text())
      const doc = parsed as IdentityBundleFile
      if (typeof doc?.version !== 'number' || !Array.isArray(doc.identities)) {
        throw new Error('shape')
      }
      setBundle({ file: file.name, doc })
      setCookies('')
      setUserAgent('')
      check.mutate(doc)
    } catch {
      setBundle(null)
      check.reset()
      toast.error(t('console:identity.restore.unreadable'))
    }
  }

  const store = useApiMutation<ImportResponse, void>(
    () =>
      apiPost<ImportResponse>(paths.identities.import, {
        platform,
        cookies: cookies.trim(),
        user_agent: userAgent.trim() || null,
        proxy_id: proxyId || null,
        reserved,
        note: note.trim() || null,
        dry_run: false,
      }),
    {
      onSuccess: async (response) => {
        const identityId = response.identity_id
        toast.success(t('console:identity.import.stored'), {
          action: identityId ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                void copyText(identityId)
              }}
            >
              {t('console:identity.drawer.copyId')}
            </Button>
          ) : undefined,
        })
        close()
        if (identityId) onImported(identityId, response.report.authenticated)
        await onDone()
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.import.failed'))
      },
    },
  )

  function clearBundle(): void {
    setBundle(null)
    check.reset()
  }

  function close(): void {
    setCookies('')
    setUserAgent('')
    setProxyId('')
    setReserved(false)
    setNote('')
    setAcknowledged(false)
    setPreview({ loading: false })
    clearBundle()
    onClose()
  }

  const report = preview.report
  const canStore =
    Boolean(report?.usable) && (!report?.authenticated || acknowledged) && !preview.loading
  const detected = report?.detected_platform ?? null
  const poolRow = pool.data?.platforms.find((row) => row.platform === platform)
  const loginOnly = poolRow?.login_required_for ?? []
  const noLoginInRotation =
    poolRow?.logins !== undefined && poolRow.logins.rotating === 0 && loginOnly.length > 0

  const bundleCheck = check.data
  const restorable = bundleCheck?.results.filter((entry) => entry.usable).length ?? 0
  const bundleHasLogins = bundleCheck?.results.some((entry) => entry.authenticated) ?? false
  const canRestore =
    bundle !== null &&
    bundleCheck !== undefined &&
    restorable > 0 &&
    (!bundleHasLogins || acknowledged)

  return (
    <Modal
      open={open}
      onClose={close}
      size="lg"
      title={t('console:identity.import.title')}
      description={t('console:identity.import.description')}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            {t('common:action.cancel')}
          </Button>
          {bundle ? (
            <Button
              variant="primary"
              loading={restore.isPending}
              disabled={!canRestore}
              onClick={() => {
                restore.mutate(bundle.doc)
              }}
            >
              {t('console:identity.restore.submit', { count: restorable })}
            </Button>
          ) : (
            <Button
              variant="primary"
              loading={store.isPending}
              disabled={!canStore}
              onClick={() => {
                store.mutate()
              }}
            >
              {t('console:identity.import.submit')}
            </Button>
          )}
        </>
      }
    >
      <div className="u-stack">
        <div className={styles.riskPanel}>
          <span className="u-row" style={{ color: 'var(--warning)' }}>
            <AlertIcon size={13} />
            <strong>{t('console:identity.import.risk.title')}</strong>
          </span>
          <p className="u-xs u-secondary">{t('console:identity.import.risk.equivalence')}</p>
          <p className="u-xs u-secondary">{t('console:identity.import.risk.storage')}</p>
          <p className="u-xs u-secondary">{t('console:identity.import.risk.dedicated')}</p>
          <p className="u-xs u-secondary">{t('console:identity.import.risk.ban')}</p>
        </div>

        {/* A file first, because restoring a pool is one action and pasting a
            jar is a dozen. Everything below is for the paste, and a loaded
            file hides it rather than leaving two half-filled forms. */}
        <div className="u-stack-sm">
          <label className="u-xs u-secondary">{t('console:identity.restore.label')}</label>
          <input
            type="file"
            accept="application/json,.json"
            className="u-xs"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void readBundle(file)
              // Cleared so choosing the same file twice fires again, which is
              // what somebody who just fixed the file expects.
              event.target.value = ''
            }}
          />
          <span className="u-xs u-muted">{t('console:identity.restore.hint')}</span>
        </div>

        {bundle ? (
          <>
            <Banner tone="accent" icon={<InfoIcon size={14} />}>
              <div className="u-stack-sm">
                <span>
                  {t('console:identity.restore.loaded', {
                    file: bundle.file,
                    count: bundle.doc.identities.length,
                  })}
                </span>
                <Button size="sm" variant="ghost" onClick={clearBundle}>
                  {t('common:action.clear')}
                </Button>
              </div>
            </Banner>
            {check.isPending ? <Skeleton height={80} radius="var(--radius)" /> : null}
            {check.error ? <ErrorState error={check.error} compact /> : null}
            {bundleCheck ? <BundlePreview result={bundleCheck} /> : null}
          </>
        ) : (
          <>
            <Select
              label={t('console:field.platform')}
              value={platform}
              onChange={(event) => {
                setPlatform(event.target.value as Platform)
              }}
              options={PLATFORMS.map((value) => ({ value, label: platformName(value) }))}
            />

            <Textarea
              label={t('console:identity.import.cookies')}
              description={`${t(`console:identity.import.needs.${platform}`)} ${t('console:identity.import.formats')}`}
              rows={7}
              required
              value={cookies}
              placeholder={PLACEHOLDERS[platform]}
              onChange={(event) => {
                setCookies(event.target.value)
              }}
            />

            {/* Offered, not done: a jar that names another platform is a
                mistake far more often than a choice, but switching under
                somebody's hands would re-judge a paste they are still typing. */}
            {detected && detected !== platform && !preview.loading ? (
              <Banner tone="caution" icon={<InfoIcon size={14} />}>
                <span className="u-row u-wrap">
                  <span>
                    {t('console:identity.import.detected', { platform: platformPhrase(detected) })}
                  </span>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setPlatform(detected)
                    }}
                  >
                    {t('console:identity.import.switchTo', { platform: platformPhrase(detected) })}
                  </Button>
                </span>
              </Banner>
            ) : null}

            <Input
              label={t('console:identity.import.userAgent')}
              description={t('console:identity.import.userAgentHint')}
              showOptional
              mono
              value={userAgent}
              onChange={(event) => {
                setUserAgent(event.target.value)
              }}
            />
          </>
        )}

        <Select
          label={t('console:field.proxy')}
          description={t('console:identity.import.proxyHint')}
          showOptional
          value={proxyId}
          onChange={(event) => {
            setProxyId(event.target.value)
          }}
          options={[
            // Not the mint dialog's "let the scheduler choose": an import with no
            // proxy is stored direct and stays direct, which on a server in
            // mainland China means it can never reach TikTok (issue 781).
            { value: '', label: t('console:identity.import.noProxy') },
            ...proxies.map((proxy) => ({
              value: proxy.id,
              label: proxy.label ?? proxy.url_masked ?? proxy.id,
              disabled: !proxy.healthy,
            })),
          ]}
        />

        {bundle ? null : (
          <>
            {preview.loading ? <Skeleton height={120} radius="var(--radius)" /> : null}
            {preview.error ? <ErrorState error={preview.error} compact /> : null}
            {report && !preview.loading ? <CookieReportView report={report} /> : null}

            <Input
              label={t('console:identity.note.label')}
              showOptional
              value={note}
              maxLength={NOTE_MAX}
              placeholder={t('console:identity.note.placeholder')}
              description={t('console:identity.note.importHint')}
              onChange={(event) => {
                setNote(event.target.value.replace(/[\r\n]+/g, ' '))
              }}
            />

            <div className="u-stack-sm">
              <Checkbox
                checked={reserved}
                onChange={(event) => {
                  setReserved(event.target.checked)
                }}
                label={t('console:identity.import.reserve')}
                hint={t('console:identity.import.reserveHint')}
              />
              {reserved && report?.authenticated && noLoginInRotation ? (
                <p className="u-xs" style={{ margin: 0, color: 'var(--caution)' }}>
                  {t('console:identity.import.reserveLastNote', {
                    platform: platformPhrase(platform),
                    endpoints: loginOnly.join(', '),
                  })}
                </p>
              ) : null}
            </div>
          </>
        )}

        {/* Asked only of a login. A guest jar is a visitor id anybody's
            browser is handed on its first visit; making the person swear it
            is a password taught them to tick the box without reading it. */}
        {(bundle ? bundleHasLogins : report?.authenticated) ? (
          <Checkbox
            checked={acknowledged}
            onChange={(event) => {
              setAcknowledged(event.target.checked)
            }}
            label={t('console:identity.import.acknowledge')}
          />
        ) : null}
        {!bundle && report?.expires_at ? (
          <p className="u-xs u-muted" style={{ margin: 0 }}>
            {t('console:identity.import.expiryNotice', {
              date: format.date(report.expires_at),
            })}
          </p>
        ) : null}
      </div>
    </Modal>
  )
}

/**
 * What the server understood from the paste.
 *
 * Each cookie's role is the server's own (`_cookie_role`), for the platform
 * chosen. The console kept a copy of those sets that knew only ByteDance
 * names, so a whole Bilibili jar - its required visitor id and its login
 * included - was labelled "other".
 */
function CookieReportView({ report }: { report: CookieReport }) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()

  const names =
    report.cookie_names.length > 0 ? report.cookie_names : Object.keys(report.cookies_masked)

  return (
    <div className={styles.reportPanel}>
      <div className="u-row u-wrap">
        <StatusBadge
          kind="health"
          value={report.usable ? 'healthy' : 'unhealthy'}
          flash={false}
          title={t('console:identity.import.usableHint')}
        />
        <Fact label={t('console:identity.import.format')} value={report.detected_format} mono />
        <Fact
          label={t('console:identity.import.browser')}
          value={
            report.browser_family
              ? `${report.browser_family}${report.browser_major ? ` ${String(report.browser_major)}` : ''}`
              : MISSING
          }
          mono
        />
        <Fact
          label={t('console:identity.import.loggedIn')}
          value={report.authenticated ? t('common:value.yes') : t('common:value.no')}
        />
        <Fact
          label={t('console:field.expiresAt')}
          value={
            report.expires_at
              ? `${format.date(report.expires_at)} · ${format.relative(report.expires_at)}`
              : MISSING
          }
        />
        <Fact label={t('console:identity.import.count')} value={format.number(names.length)} mono />
      </div>

      {report.missing_required.length > 0 ? (
        <p className="u-row u-xs" style={{ color: 'var(--danger)', alignItems: 'flex-start' }}>
          <AlertIcon size={12} />
          <span className="u-mono">
            {t('console:identity.import.missing')} {report.missing_required.join(', ')}
          </span>
        </p>
      ) : null}

      {report.warnings.map((warning) => (
        <p key={warning} className="u-xs" style={{ color: 'var(--caution)' }}>
          {warning}
        </p>
      ))}

      <ul className={styles.reportCookies}>
        {names.map((name) => {
          const role = report.cookie_roles?.[name] ?? 'other'
          return (
            <li key={name} className="u-row-between u-nowrap">
              <span className="u-row">
                <span className="u-mono">{name}</span>
                <span className="u-xs" style={{ color: ROLE_COLOR[role] }}>
                  {t(`console:identity.import.role.${role}`)}
                </span>
              </span>
              <MaskedSecret value={report.cookies_masked[name] ?? null} />
            </li>
          )
        })}
      </ul>
      <p className="u-xs u-muted">{t('console:identity.import.maskNote')}</p>
    </div>
  )
}

/**
 * The file, entry by entry, before anything is stored.
 *
 * Skipped entries carry the route's own reason. A login whose session has
 * lapsed is restored all the same - it still signs as a guest - and is said
 * here so nobody restores a file expecting the logins in it to work.
 */
function BundlePreview({ result }: { result: BundleImportResult }) {
  const { t } = useTranslation('console')
  const format = useFormatters()
  const platformName = usePlatformName()

  const usable = result.results.filter((entry) => entry.usable)
  const skipped = result.results.filter((entry) => !entry.usable)
  const lapsed = usable.filter((entry) => hasLapsed(entry.expires_at))
  const logins = usable.filter((entry) => entry.authenticated).length

  return (
    <div className={styles.reportPanel}>
      <div className="u-row u-wrap">
        <Fact
          label={t('identity.restore.restorable')}
          value={t('identity.restore.ofTotal', { count: usable.length, total: result.submitted })}
        />
        <Fact label={t('identity.restore.logins')} value={format.number(logins)} mono />
        {skipped.length > 0 ? (
          <Fact label={t('identity.restore.skippedLabel')} value={format.number(skipped.length)} mono />
        ) : null}
      </div>
      {skipped.length > 0 || lapsed.length > 0 ? (
        <ul className={styles.reportCookies}>
          {skipped.map((entry) => (
            <li key={`skip-${entry.index}`} className={styles.bundleEntry}>
              <span className="u-mono u-xs">
                {t('identity.restore.entry', {
                  number: entry.index + 1,
                  platform: entry.platform ? platformName(entry.platform) : MISSING,
                })}
              </span>
              <span className="u-xs" style={{ color: 'var(--danger)' }}>
                {t(`identity.restore.reason.${entry.reason ?? 'unusable'}`, {
                  defaultValue: entry.reason ?? '',
                })}
                {entry.missing_required && entry.missing_required.length > 0
                  ? ` · ${t('identity.restore.missing', { names: entry.missing_required.join(', ') })}`
                  : ''}
              </span>
            </li>
          ))}
          {lapsed.map((entry) => (
            <li key={`lapsed-${entry.index}`} className={styles.bundleEntry}>
              <span className="u-mono u-xs">
                {t('identity.restore.entry', {
                  number: entry.index + 1,
                  platform: entry.platform ? platformName(entry.platform) : MISSING,
                })}
              </span>
              <span className="u-xs" style={{ color: 'var(--caution)' }}>
                {t('identity.restore.lapsed', { date: format.date(entry.expires_at ?? '') })}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
