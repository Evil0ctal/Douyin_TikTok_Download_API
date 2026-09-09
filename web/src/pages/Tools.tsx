import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  CodeBlock,
  SigningStages,
  ErrorState,
  Field,
  Input,
  PageHeader,
  Select,
  Textarea,
  useToast,
} from '@/components'
import { useApiMutation } from '@/hooks'
import { apiGet, apiPost, isApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import type { SigningStage } from '@/components'
import type { Platform } from '@/lib/types'


/**
 * The building blocks, as three forms.
 *
 * These are the pieces the service uses on every request, published because
 * this is an open-source project and people build on them. The page exists to
 * make them explorable without writing a curl line, and to put the one warning
 * that matters where it will actually be read.
 *
 * That warning: a signature alone is not enough. Both platforms answer a
 * *session*, so a correct signature sent with no cookies gets 200 and an empty
 * body - measured, and the first thing anyone hits. The signing form therefore
 * says what else has to line up, and the minting form is what produces it.
 */

type ToolTab = 'sign' | 'parse' | 'identity'

const PLATFORMS: readonly Platform[] = ['douyin', 'tiktok']

export default function Tools() {
  const { t } = useTranslation('console')
  const [tab, setTab] = useState<ToolTab>('sign')

  return (
    <div className="u-page">
      <PageHeader title={t('tools.title')} description={t('tools.description')} />

      <Card>
        <div className="u-row" role="tablist" aria-label={t('tools.title')}>
          {(['sign', 'parse', 'identity'] as const).map((id) => (
            <Button
              key={id}
              role="tab"
              aria-selected={tab === id}
              variant={tab === id ? 'primary' : 'ghost'}
              onClick={() => setTab(id)}
            >
              {t(`tools.tab.${id}`)}
            </Button>
          ))}
        </div>
      </Card>

      {tab === 'sign' && <SignForm />}
      {tab === 'parse' && <ParseForm />}
      {tab === 'identity' && <IdentityForm />}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Sign                                                                        */
/* -------------------------------------------------------------------------- */

interface SignResult {
  platform: string
  signed_url: string
  query: string
  params: Record<string, string>
  headers: Record<string, string>
  user_agent: string
  algorithm: string
  /** Optional: an older build of the API answers the seven fields and nothing else. */
  stages?: readonly SigningStage[]
}

function SignForm() {
  const { t } = useTranslation('console')
  const toast = useToast()
  const [platform, setPlatform] = useState<Platform>('douyin')
  const [url, setUrl] = useState('')
  const [userAgent, setUserAgent] = useState('')
  const [msToken, setMsToken] = useState('')
  const [cookies, setCookies] = useState('')

  const sign = useApiMutation<SignResult, void>(
    () =>
      apiPost<SignResult>(paths.tools.sign, {
        platform,
        url: url.trim(),
        user_agent: userAgent.trim() || null,
        ms_token: msToken.trim() || null,
        cookies: cookies.trim() || null,
      }),
    { onError: (error) => toast.apiError(error) },
  )

  return (
    <>
      <Card title={t('tools.sign.title')} description={t('tools.sign.description')}>
        <div className="u-stack">
          <Field id="sign-platform" label={t('tools.field.platform')}>
            <Select value={platform} onChange={(event) => setPlatform(event.target.value as Platform)}>
              {PLATFORMS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </Field>

          <Field id="sign-url" label={t('tools.field.apiUrl')} description={t('tools.sign.urlHint')}>
            <Textarea
              rows={3}
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://www.douyin.com/aweme/v1/web/aweme/detail/?aid=6383&aweme_id=..."
            />
          </Field>

          <Field id="sign-ua" label={t('tools.field.userAgent')} description={t('tools.sign.userAgentHint')}>
            <Input
              value={userAgent}
              onChange={(event) => setUserAgent(event.target.value)}
              placeholder={t('tools.sign.userAgentPlaceholder')}
            />
          </Field>

          <Field id="sign-mstoken" label={t('tools.field.msToken')} description={t('tools.sign.msTokenHint')}>
            <Input
              value={msToken}
              onChange={(event) => setMsToken(event.target.value)}
              placeholder={t('tools.sign.msTokenPlaceholder')}
            />
          </Field>

          <Field
            id="sign-cookies"
            label={t('tools.field.cookies')}
            description={t('tools.sign.cookiesHint')}
          >
            <Textarea
              rows={3}
              value={cookies}
              onChange={(event) => setCookies(event.target.value)}
              placeholder={t('tools.sign.cookiesPlaceholder')}
            />
          </Field>

          <Button
            variant="primary"
            loading={sign.isPending}
            disabled={!url.trim()}
            onClick={() => sign.mutate()}
          >
            {t('tools.sign.action')}
          </Button>
        </div>
      </Card>

      <Card title={t('tools.sign.checklistTitle')}>
        <ol className="u-stack-sm">
          <li>{t('tools.sign.checkUserAgent')}</li>
          <li>{t('tools.sign.checkTls')}</li>
          <li>{t('tools.sign.checkCookies')}</li>
        </ol>
      </Card>

      {sign.data && (
        <>
          <Card title={t('tools.sign.signedUrl')}>
            <CodeBlock code={sign.data.signed_url} language="text" />
          </Card>
          {sign.data.stages && sign.data.stages.length > 0 ? (
            <Card
              title={t('tools.sign.stages.title')}
              description={t('tools.sign.stages.description')}
              flush
            >
              <SigningStages stages={sign.data.stages} />
            </Card>
          ) : null}
          <Card title={t('tools.sign.added', { algorithm: sign.data.algorithm })}>
            <CodeBlock json={sign.data.params} />
          </Card>
          {Object.keys(sign.data.headers).length > 0 && (
            <Card title={t('tools.sign.headers')} description={t('tools.sign.headersHint')}>
              <CodeBlock json={sign.data.headers} />
            </Card>
          )}
          <Card title={t('tools.field.userAgent')} description={t('tools.sign.userAgentEcho')}>
            <CodeBlock code={sign.data.user_agent} language="text" />
          </Card>
        </>
      )}
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* Stages                                                                      */
/* -------------------------------------------------------------------------- */


/* -------------------------------------------------------------------------- */
/* Parse                                                                       */
/* -------------------------------------------------------------------------- */

interface ParseResult {
  allowed: boolean
  platform: string | null
  resource: string
  resource_id: string | null
  handle: string | null
  content_kind: string | null
  url: string | null
  needs_expansion: boolean
}

function ParseForm() {
  const { t } = useTranslation('console')
  const [text, setText] = useState('')

  const parse = useApiMutation<ParseResult, void>(() =>
    apiGet<ParseResult>(paths.tools.parseUrl, { params: { url: text.trim() } }),
  )

  return (
    <>
      <Card title={t('tools.parse.title')} description={t('tools.parse.description')}>
        <div className="u-stack">
          <Field id="parse-link" label={t('tools.field.link')} description={t('tools.parse.hint')}>
            <Textarea
              rows={3}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="https://v.douyin.com/iRNBho6G/"
            />
          </Field>
          <Button
            variant="primary"
            loading={parse.isPending}
            disabled={!text.trim()}
            onClick={() => parse.mutate()}
          >
            {t('tools.parse.action')}
          </Button>
        </div>
      </Card>

      {parse.error && !isApiError(parse.error) && <ErrorState error={parse.error} />}

      {parse.data && (
        <Card title={t('tools.parse.result')}>
          {parse.data.needs_expansion && <p className="u-muted">{t('tools.parse.shortLink')}</p>}
          <CodeBlock json={parse.data} />
        </Card>
      )}
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* Identity                                                                    */
/* -------------------------------------------------------------------------- */

interface IdentityResult {
  platform: string
  cookies: Record<string, string>
  fingerprint: Record<string, unknown>
  exit_ip: string | null
}

function IdentityForm() {
  const { t } = useTranslation('console')
  const toast = useToast()
  const [platform, setPlatform] = useState<Platform>('douyin')
  const [proxy, setProxy] = useState('')

  const mint = useApiMutation<IdentityResult, void>(
    () =>
      apiPost<IdentityResult>(paths.tools.identity, {
        platform,
        proxy: proxy.trim() || null,
      }),
    { onError: (error) => toast.apiError(error) },
  )

  return (
    <>
      <Card title={t('tools.identity.title')} description={t('tools.identity.description')}>
        <div className="u-stack">
          <Field id="identity-platform" label={t('tools.field.platform')}>
            <Select value={platform} onChange={(event) => setPlatform(event.target.value as Platform)}>
              {PLATFORMS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </Field>

          <Field id="identity-proxy" label={t('tools.field.proxy')} description={t('tools.identity.proxyHint')}>
            <Input
              value={proxy}
              onChange={(event) => setProxy(event.target.value)}
              placeholder="http://user:pass@host:port"
            />
          </Field>

          <Button variant="primary" loading={mint.isPending} onClick={() => mint.mutate()}>
            {t('tools.identity.action')}
          </Button>
          <p className="u-xs u-muted">{t('tools.identity.slow')}</p>
        </div>
      </Card>

      {mint.data && (
        <>
          <Card title={t('tools.identity.cookies')} description={t('tools.identity.cookiesHint')}>
            <CodeBlock json={mint.data.cookies} />
          </Card>
          <Card
            title={t('tools.identity.fingerprint')}
            description={t('tools.identity.fingerprintHint')}
          >
            <CodeBlock json={mint.data.fingerprint} />
          </Card>
        </>
      )}
    </>
  )
}
