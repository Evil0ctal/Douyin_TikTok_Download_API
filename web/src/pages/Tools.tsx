import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  CodeBlock,
  CopyableId,
  DataTable,
  SigningStages,
  ErrorState,
  Field,
  Input,
  MetricTile,
  PageHeader,
  Select,
  SignatureDecode,
  Textarea,
  useToast,
} from '@/components'
import { useApiMutation, useFormatters } from '@/hooks'
import { apiGet, apiPost, isApiError } from '@/lib/api'
import { paths } from '@/lib/endpoints'
import { MISSING } from '@/lib/format'
import type { DecodedParameter, SigningStage } from '@/components'
import { SIGNING_EXAMPLES } from '@/lib/signingExamples'
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

type ToolTab = 'sign' | 'decode' | 'parse' | 'batch' | 'identity'

const PLATFORMS: readonly Platform[] = ['douyin', 'tiktok']

/**
 * What the decode tab starts with when the sign tab hands off to it.
 *
 * The two forms are inverses, and the most useful thing anyone can do with them
 * is run one straight into the other - sign a URL, then read the result back
 * and watch every check come out green. Retyping a 2,000 character signed URL
 * to do that is not a thing anybody would do, so the sign result offers a
 * button instead.
 */
interface DecodeSeed {
  value: string
  userAgent: string
}

export default function Tools() {
  const { t } = useTranslation('console')
  const [tab, setTab] = useState<ToolTab>('sign')
  const [seed, setSeed] = useState<DecodeSeed | null>(null)

  return (
    <div className="u-page">
      <PageHeader title={t('tools.title')} description={t('tools.description')} />

      <Card>
        <div className="u-row" role="tablist" aria-label={t('tools.title')}>
          {(['sign', 'decode', 'parse', 'batch', 'identity'] as const).map((id) => (
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

      {tab === 'sign' && (
        <SignForm
          onDecode={(next) => {
            setSeed(next)
            setTab('decode')
          }}
        />
      )}
      {tab === 'decode' && <DecodeForm seed={seed} />}
      {tab === 'parse' && <ParseForm />}
      {tab === 'batch' && <BatchForm />}
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

function SignForm({ onDecode }: { onDecode: (seed: DecodeSeed) => void }) {
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

  /** Fill every field from a real browser request, credentials stripped. */
  const loadExample = (name: Platform): void => {
    const example = SIGNING_EXAMPLES[name]
    setPlatform(example.platform)
    setUrl(example.url)
    setUserAgent(example.userAgent)
    setMsToken(example.msToken)
    setCookies(example.cookies)
  }

  return (
    <>
      <Card title={t('tools.sign.title')} description={t('tools.sign.description')}>
        <div className="u-stack">
          {/* Above the fields, not beside the submit button: the question it
              answers - "what does a real one of these look like" - is the one
              somebody has before they start typing, not after. */}
          <Field id="sign-example" label={t('tools.sign.exampleLabel')} description={t('tools.sign.exampleHint')}>
            <div className="u-row">
              {PLATFORMS.map((name) => (
                <Button
                  key={name}
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    loadExample(name)
                  }}
                >
                  {t('tools.sign.loadExample', { platform: name })}
                </Button>
              ))}
            </div>
          </Field>

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
            <div className="u-row" style={{ marginTop: 'var(--space-3)' }}>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  onDecode({
                    value: sign.data.signed_url,
                    userAgent: sign.data.user_agent,
                  })
                }}
              >
                {t('tools.sign.decodeThis')}
              </Button>
            </div>
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
/* Decode                                                                      */
/* -------------------------------------------------------------------------- */

interface DecodeResult {
  source: 'url' | 'parameter'
  platform: string | null
  user_agent: string | null
  parameters: readonly DecodedParameter[]
}

/** The parameters the endpoint can be told to read a value as. `auto` is the default. */
const DECODE_PARAMETERS = [
  'a_bogus',
  'X-Bogus',
  'X-Gnarly',
  'X-Dynosaur',
  'msToken',
  'x-secsdk-web-signature',
  'verifyFp',
] as const

/**
 * The inverse of the form above it.
 *
 * It exists because this project reversed these algorithms itself rather than
 * vendoring somebody's port, and the dividend of owning them is that they can
 * be explained. A reader who has only ever seen `a_bogus` as 160 opaque
 * characters can watch a clock, a screen size and Douyin's own `aid` come back
 * out of it.
 *
 * The User-Agent field is not decoration and is worth filling in even when the
 * answer seems obvious. Both platforms hash the UA into the signature, and "the
 * UA I am sending is not the UA I signed with" is the single most common way a
 * hand-built request fails while looking perfectly correct.
 */
function DecodeForm({ seed }: { seed: DecodeSeed | null }) {
  const { t } = useTranslation('console')
  const toast = useToast()
  const [value, setValue] = useState(seed?.value ?? '')
  const [userAgent, setUserAgent] = useState(seed?.userAgent ?? '')
  const [parameter, setParameter] = useState('')

  const decode = useApiMutation<DecodeResult, void>(
    () =>
      apiPost<DecodeResult>(paths.tools.decode, {
        value: value.trim(),
        user_agent: userAgent.trim() || null,
        parameter: parameter || null,
      }),
    { onError: (error) => toast.apiError(error) },
  )

  return (
    <>
      <Card title={t('tools.decode.title')} description={t('tools.decode.description')}>
        <div className="u-stack">
          <Field
            id="decode-value"
            label={t('tools.decode.valueLabel')}
            description={t('tools.decode.valueHint')}
          >
            <Textarea
              rows={4}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder={t('tools.decode.valuePlaceholder')}
            />
          </Field>

          <Field
            id="decode-ua"
            label={t('tools.field.userAgent')}
            description={t('tools.decode.userAgentHint')}
          >
            <Input
              value={userAgent}
              onChange={(event) => setUserAgent(event.target.value)}
              placeholder={t('tools.sign.userAgentPlaceholder')}
            />
          </Field>

          <Field
            id="decode-parameter"
            label={t('tools.decode.parameterLabel')}
            description={t('tools.decode.parameterHint')}
          >
            <Select value={parameter} onChange={(event) => setParameter(event.target.value)}>
              <option value="">{t('tools.decode.parameterAuto')}</option>
              {DECODE_PARAMETERS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </Field>

          <Button
            variant="primary"
            loading={decode.isPending}
            disabled={!value.trim()}
            onClick={() => decode.mutate()}
          >
            {t('tools.decode.action')}
          </Button>
        </div>
      </Card>

      {/* Said before the result rather than after somebody has misread one.
          Three different things arrive looking like table rows, and taking the
          second for the first is the mistake this panel exists to prevent. */}
      <Card title={t('tools.decode.meaningTitle')}>
        <ul className="u-stack-sm">
          <li>{t('tools.decode.meaningRecovered')}</li>
          <li>{t('tools.decode.meaningBound')}</li>
          <li>{t('tools.decode.meaningIssued')}</li>
        </ul>
      </Card>

      {decode.data ? (
        <Card
          title={t('tools.decode.resultTitle', { count: decode.data.parameters.length })}
          description={t(`tools.decode.source.${decode.data.source}`)}
          flush
        >
          <SignatureDecode parameters={decode.data.parameters} />
        </Card>
      ) : null}
    </>
  )
}


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
/* Batch                                                                       */
/* -------------------------------------------------------------------------- */

interface BatchItem {
  input: string
  kind: 'link' | 'short_link' | 'content_id' | 'bad_id' | 'unknown'
  platform: string | null
  resource: string | null
  resource_id: string | null
  handle: string | null
  url: string | null
  needs_expansion: boolean
  /**
   * When the id was issued. Both platforms mint post ids whose high 32 bits are
   * a Unix second, so this comes out of the id itself rather than out of a
   * request. It is at or a little before the publication time, never after.
   */
  minted_at: string | null
}

interface BatchResult {
  items: BatchItem[]
  total: number
  counts: Record<string, number>
  truncated: boolean
}

/** Newest first is wrong here: the answer has to line up with what was pasted. */
const BATCH_KINDS = ['content_id', 'link', 'short_link', 'bad_id', 'unknown'] as const

function BatchForm() {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()
  const [text, setText] = useState('')

  const parse = useApiMutation<BatchResult, void>(
    () => apiPost<BatchResult>(paths.tools.parseBatch, { text }),
    {
      onError: (error) => {
        toast.apiError(error)
      },
    },
  )

  const lines = text.split('\n').filter((line) => line.trim()).length

  return (
    <>
      <Card title={t('console:tools.batch.title')} description={t('console:tools.batch.description')}>
        <div className="u-stack">
          <Field
            id="batch-input"
            label={t('console:tools.batch.field')}
            description={t('console:tools.batch.hint')}
          >
            <Textarea
              rows={10}
              value={text}
              onChange={(event) => {
                setText(event.target.value)
              }}
              placeholder={'7123456789012345678\nhttps://www.douyin.com/video/7123456789012345678'}
            />
          </Field>
          <div className="u-row">
            <Button
              variant="primary"
              loading={parse.isPending}
              disabled={lines === 0}
              onClick={() => {
                parse.mutate()
              }}
            >
              {t('console:tools.batch.action', { count: lines })}
            </Button>
            {parse.data ? (
              <Button
                variant="secondary"
                onClick={() => {
                  const ids = parse.data.items
                    .filter((item) => item.resource_id && item.kind !== 'bad_id')
                    .map((item) => item.resource_id)
                    .join('\n')
                  void navigator.clipboard.writeText(ids)
                  toast.success(t('console:tools.batch.copied'))
                }}
              >
                {t('console:tools.batch.copyIds')}
              </Button>
            ) : null}
          </div>
        </div>
      </Card>

      {parse.error && !isApiError(parse.error) && <ErrorState error={parse.error} />}

      {parse.data ? (
        <>
          <div className="u-grid-metrics">
            {BATCH_KINDS.filter((kind) => parse.data.counts[kind]).map((kind) => (
              <MetricTile
                key={kind}
                label={t(`console:tools.batch.kind.${kind}`)}
                value={String(parse.data.counts[kind])}
              />
            ))}
          </div>

          {parse.data.truncated ? (
            <Card>
              <p className="u-muted" style={{ margin: 0 }}>
                {t('console:tools.batch.truncated', { total: parse.data.total })}
              </p>
            </Card>
          ) : null}

          <Card flush>
            <DataTable
              columns={[
                {
                  id: 'input',
                  header: t('console:tools.batch.column.input'),
                  width: '40%',
                  cell: (row) => (
                    <span className="u-mono u-truncate" title={row.input}>
                      {row.input}
                    </span>
                  ),
                },
                {
                  id: 'kind',
                  header: t('console:tools.batch.column.kind'),
                  cell: (row) => (
                    <span className={row.kind === 'bad_id' || row.kind === 'unknown' ? 'u-danger' : undefined}>
                      {t(`console:tools.batch.kind.${row.kind}`)}
                    </span>
                  ),
                },
                {
                  id: 'platform',
                  header: t('console:tools.batch.column.platform'),
                  cell: (row) => <span className="u-mono">{row.platform ?? MISSING}</span>,
                },
                {
                  id: 'resource_id',
                  header: t('console:tools.batch.column.id'),
                  width: '25%',
                  cell: (row) =>
                    row.resource_id ? (
                      <CopyableId value={row.resource_id} />
                    ) : (
                      <span className="u-muted">{MISSING}</span>
                    ),
                },
                {
                  id: 'minted_at',
                  header: t('console:tools.batch.column.minted'),
                  cell: (row) =>
                    row.minted_at ? (
                      format.dateTime(row.minted_at)
                    ) : (
                      <span className="u-muted">{MISSING}</span>
                    ),
                },
              ]}
              rows={parse.data.items}
              getRowId={(row) => row.input}
              caption={t('console:tools.batch.title')}
            />
          </Card>
        </>
      ) : null}
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
