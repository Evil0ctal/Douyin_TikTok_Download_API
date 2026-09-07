import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Button,
  Card,
  CopyableId,
  EmptyState,
  ErrorState,
  ExternalIcon,
  PageHeader,
  RefreshIcon,
  Skeleton,
} from '@/components'
import { useApiQuery } from '@/hooks'
import { paths } from '@/lib/endpoints'
import { currentLanguage } from '@/lib/language'

/**
 * Embedded API reference.
 *
 * V4's /docs was one of the most used entry points in the whole project, so the
 * console embeds it rather than linking away, pointed at the document for the
 * interface language the user is already reading (docs/design/14-i18n.md).
 *
 * Swagger UI ships an opinionated light palette that looks like a different
 * product bolted on. Everything below the toolbar is therefore restyled from
 * our tokens, and the override is CSS-only: the widget keeps its own behaviour,
 * we only take the colours (docs/design/12-design-system.md).
 *
 * The assets come from the same CDN the server's own /docs page uses. When that
 * is unreachable - an air-gapped deployment, a blocked CDN - the page says so
 * and offers the server-rendered page and the raw document instead of leaving
 * an empty frame.
 */

const SWAGGER_VERSION = '5.17.14'
const SWAGGER_BASE = `https://cdn.jsdelivr.net/npm/swagger-ui-dist@${SWAGGER_VERSION}`
const STYLE_ID = 'dtk-swagger-assets-css'
const SCRIPT_ID = 'dtk-swagger-assets-js'
const THEME_ID = 'dtk-swagger-theme'
const ASSET_TIMEOUT_MS = 15_000

const HTTP_METHODS = ['get', 'put', 'post', 'delete', 'patch', 'options', 'head', 'trace']

interface OpenApiDoc {
  openapi?: string
  info?: {
    title?: string
    version?: string
    description?: string
    'x-language'?: string
  }
  paths?: Record<string, Record<string, unknown>>
}

/** The subset of the Swagger UI factory this page uses. */
type SwaggerFactory = ((options: Record<string, unknown>) => unknown) & {
  presets?: { apis?: unknown }
}

interface SwaggerWindow {
  SwaggerUIBundle?: SwaggerFactory
}

function swaggerFactory(): SwaggerFactory | undefined {
  return (window as unknown as SwaggerWindow).SwaggerUIBundle
}

function loadOnce(
  tag: 'link' | 'script',
  id: string,
  attributes: Record<string, string>,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = document.getElementById(id)
    if (existing) {
      if (existing.dataset['loaded'] === 'true') {
        resolve()
        return
      }
      existing.addEventListener('load', () => {
        resolve()
      })
      existing.addEventListener('error', () => {
        reject(new Error(id))
      })
      return
    }

    const element = document.createElement(tag)
    element.id = id
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value)
    element.addEventListener('load', () => {
      element.dataset['loaded'] = 'true'
      resolve()
    })
    element.addEventListener('error', () => {
      reject(new Error(id))
    })
    document.head.appendChild(element)
  })
}

function withTimeout(promise: Promise<void>, ms: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      reject(new Error('timeout'))
    }, ms)
    promise.then(
      () => {
        window.clearTimeout(timer)
        resolve()
      },
      (error: unknown) => {
        window.clearTimeout(timer)
        reject(error instanceof Error ? error : new Error(String(error)))
      },
    )
  })
}

/**
 * Swagger's palette, replaced by ours.
 *
 * Only colour, typography and radius are touched. Method badges use the
 * categorical series rather than the status colours, so a green POST can never
 * be mistaken for a success indicator - the one exception is DELETE, where
 * danger is the accurate meaning.
 */
const THEME_CSS = `
.dtk-swagger { color: var(--text); }
.dtk-swagger .swagger-ui,
.dtk-swagger .swagger-ui .info li,
.dtk-swagger .swagger-ui .info p,
.dtk-swagger .swagger-ui .info table,
.dtk-swagger .swagger-ui label,
.dtk-swagger .swagger-ui .parameter__name,
.dtk-swagger .swagger-ui .response-col_status,
.dtk-swagger .swagger-ui .opblock-description-wrapper p,
.dtk-swagger .swagger-ui .markdown p {
  color: var(--text);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
}
.dtk-swagger .swagger-ui .info .title,
.dtk-swagger .swagger-ui .opblock-tag,
.dtk-swagger .swagger-ui h4,
.dtk-swagger .swagger-ui h5,
.dtk-swagger .swagger-ui .model-title {
  color: var(--text);
  font-family: var(--font-sans);
}
.dtk-swagger .swagger-ui .opblock-tag { border-bottom: 1px solid var(--border-subtle); }
.dtk-swagger .swagger-ui .opblock-tag small,
.dtk-swagger .swagger-ui .parameter__type,
.dtk-swagger .swagger-ui .parameter__in,
.dtk-swagger .swagger-ui .response-col_description__inner div.renderedMarkdown p,
.dtk-swagger .swagger-ui .tab li { color: var(--text-secondary); }
.dtk-swagger .swagger-ui .opblock {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: none;
  margin-bottom: var(--space-2);
}
.dtk-swagger .swagger-ui .opblock .opblock-summary { border-color: var(--border); }
.dtk-swagger .swagger-ui .opblock .opblock-summary-path,
.dtk-swagger .swagger-ui .opblock .opblock-summary-path__deprecated,
.dtk-swagger .swagger-ui .prop-type,
.dtk-swagger .swagger-ui .model,
.dtk-swagger .swagger-ui code,
.dtk-swagger .swagger-ui .microlight {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--text);
}
.dtk-swagger .swagger-ui .opblock .opblock-summary-description { color: var(--text-secondary); }
.dtk-swagger .swagger-ui .opblock .opblock-summary-method {
  background: var(--neutral);
  color: var(--accent-text);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  text-shadow: none;
}
.dtk-swagger .swagger-ui .opblock.opblock-get { border-color: var(--series-2); background: var(--bg-raised); }
.dtk-swagger .swagger-ui .opblock.opblock-get .opblock-summary-method { background: var(--series-2); }
.dtk-swagger .swagger-ui .opblock.opblock-post { border-color: var(--series-6); background: var(--bg-raised); }
.dtk-swagger .swagger-ui .opblock.opblock-post .opblock-summary-method { background: var(--series-6); }
.dtk-swagger .swagger-ui .opblock.opblock-put { border-color: var(--series-5); background: var(--bg-raised); }
.dtk-swagger .swagger-ui .opblock.opblock-put .opblock-summary-method { background: var(--series-5); }
.dtk-swagger .swagger-ui .opblock.opblock-patch { border-color: var(--series-3); background: var(--bg-raised); }
.dtk-swagger .swagger-ui .opblock.opblock-patch .opblock-summary-method { background: var(--series-3); }
.dtk-swagger .swagger-ui .opblock.opblock-delete { border-color: var(--danger); background: var(--bg-raised); }
.dtk-swagger .swagger-ui .opblock.opblock-delete .opblock-summary-method { background: var(--danger); }
.dtk-swagger .swagger-ui .opblock .opblock-section-header {
  background: var(--bg-overlay);
  border-color: var(--border-subtle);
  box-shadow: none;
}
.dtk-swagger .swagger-ui .opblock-body pre.microlight,
.dtk-swagger .swagger-ui .highlight-code > .microlight,
.dtk-swagger .swagger-ui .model-example,
.dtk-swagger .swagger-ui .body-param__example {
  background: var(--bg-inset);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  color: var(--text);
}
.dtk-swagger .swagger-ui .microlight span { color: var(--text) !important; }
.dtk-swagger .swagger-ui section.models,
.dtk-swagger .swagger-ui .model-box,
.dtk-swagger .swagger-ui .dialog-ux .modal-ux {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
}
.dtk-swagger .swagger-ui section.models .model-container { background: var(--bg-inset); }
.dtk-swagger .swagger-ui .scheme-container,
.dtk-swagger .swagger-ui .auth-wrapper .authorize {
  background: transparent;
  box-shadow: none;
}
.dtk-swagger .swagger-ui table thead tr td,
.dtk-swagger .swagger-ui table thead tr th {
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-subtle);
  font-family: var(--font-sans);
  font-size: var(--text-xs);
}
.dtk-swagger .swagger-ui .btn {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  box-shadow: none;
  font-family: var(--font-sans);
}
.dtk-swagger .swagger-ui .btn.execute,
.dtk-swagger .swagger-ui .btn.authorize {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-text);
}
.dtk-swagger .swagger-ui .btn.authorize svg { fill: var(--accent-text); }
.dtk-swagger .swagger-ui select,
.dtk-swagger .swagger-ui input[type='text'],
.dtk-swagger .swagger-ui input[type='password'],
.dtk-swagger .swagger-ui input[type='email'],
.dtk-swagger .swagger-ui input[type='search'],
.dtk-swagger .swagger-ui textarea {
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-family: var(--font-mono);
  box-shadow: none;
}
.dtk-swagger .swagger-ui .response-control-media-type--accept-controller select { border-color: var(--success); }
.dtk-swagger .swagger-ui svg:not(:root) { fill: currentColor; }
.dtk-swagger .swagger-ui .info a,
.dtk-swagger .swagger-ui a.nostyle { color: var(--accent); }
.dtk-swagger .swagger-ui .opblock-summary:focus-visible,
.dtk-swagger .swagger-ui button:focus-visible,
.dtk-swagger .swagger-ui a:focus-visible,
.dtk-swagger .swagger-ui select:focus-visible,
.dtk-swagger .swagger-ui input:focus-visible {
  outline: var(--focus-ring-width) solid var(--accent);
  outline-offset: var(--focus-ring-offset);
}
.dtk-swagger .swagger-ui .loading-container .loading::after { color: var(--text-secondary); }
/* Swagger paints the document body; the console owns it. */
body { background: var(--bg-base); color: var(--text); font-family: var(--font-sans); }
`

export default function ApiDocs() {
  const { t, i18n } = useTranslation(['console', 'common'])
  const language = i18n.language.startsWith('zh') ? 'zh' : currentLanguage()

  const container = useRef<HTMLDivElement | null>(null)
  const [assets, setAssets] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [attempt, setAttempt] = useState(0)

  const specUrl = `${paths.docs.openapi}?lang=${language}`

  const spec = useApiQuery<OpenApiDoc>({
    key: ['openapi', language],
    path: paths.docs.openapi,
    params: { lang: language },
    staleTime: 5 * 60_000,
  })

  const summary = useMemo(() => {
    const pathEntries = Object.entries(spec.data?.paths ?? {})
    const operations = pathEntries.reduce((total, [, methods]) => {
      const keys = Object.keys(methods ?? {}).filter((key) =>
        HTTP_METHODS.includes(key.toLowerCase()),
      )
      return total + keys.length
    }, 0)
    return { paths: pathEntries.length, operations }
  }, [spec.data])

  // Token override lives as long as the page does.
  useEffect(() => {
    if (document.getElementById(THEME_ID)) return
    const style = document.createElement('style')
    style.id = THEME_ID
    style.textContent = THEME_CSS
    document.head.appendChild(style)
    return () => {
      document.getElementById(THEME_ID)?.remove()
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setAssets('loading')

    const load = async (): Promise<void> => {
      await withTimeout(
        loadOnce('link', STYLE_ID, {
          rel: 'stylesheet',
          href: `${SWAGGER_BASE}/swagger-ui.css`,
        }),
        ASSET_TIMEOUT_MS,
      )
      await withTimeout(
        loadOnce('script', SCRIPT_ID, {
          src: `${SWAGGER_BASE}/swagger-ui-bundle.js`,
          crossorigin: 'anonymous',
        }),
        ASSET_TIMEOUT_MS,
      )
    }

    void load().then(
      () => {
        if (!cancelled) setAssets(swaggerFactory() ? 'ready' : 'failed')
      },
      () => {
        if (!cancelled) setAssets('failed')
      },
    )

    return () => {
      cancelled = true
    }
  }, [attempt])

  useEffect(() => {
    if (assets !== 'ready') return
    const node = container.current
    const factory = swaggerFactory()
    if (!node || !factory) return

    // The node is rendered empty and never receives React children, so clearing
    // it between renders cannot fight the reconciler.
    node.replaceChildren()
    factory({
      domNode: node,
      url: specUrl,
      presets: factory.presets?.apis ? [factory.presets.apis] : [],
      layout: 'BaseLayout',
      deepLinking: true,
      docExpansion: 'list',
      defaultModelsExpandDepth: 0,
      displayRequestDuration: true,
      persistAuthorization: true,
      tryItOutEnabled: true,
      withCredentials: true,
    })

    return () => {
      node.replaceChildren()
    }
  }, [assets, specUrl])

  const retry = useCallback(() => {
    setAttempt((value) => value + 1)
  }, [])

  const openExternal = (href: string): void => {
    window.open(href, '_blank', 'noopener,noreferrer')
  }

  return (
    <div className="u-stack-lg">
      <PageHeader
        title={t('page.apiDocs.title')}
        description={t('page.apiDocs.description')}
        badge={
          spec.data?.info?.version ? (
            <span className="u-mono u-xs u-muted">v{spec.data.info.version}</span>
          ) : null
        }
        actions={
          <>
            <Button
              variant="ghost"
              icon={<ExternalIcon />}
              onClick={() => {
                openExternal(`${paths.docs.swagger}?lang=${language}`)
              }}
            >
              {t('docs.openSwagger')}
            </Button>
            <Button
              variant="ghost"
              icon={<ExternalIcon />}
              onClick={() => {
                openExternal(`${paths.docs.redoc}?lang=${language}`)
              }}
            >
              {t('docs.openRedoc')}
            </Button>
          </>
        }
      />

      <Card title={t('docs.specTitle')} description={t('docs.specDescription')}>
        {spec.isLoading ? (
          <div className="u-stack-sm">
            <Skeleton width={220} height={14} />
            <Skeleton width={160} height={14} />
          </div>
        ) : spec.isError ? (
          <ErrorState
            error={spec.error}
            onRetry={() => {
              void spec.refetch()
            }}
            compact
          />
        ) : summary.operations === 0 ? (
          <EmptyState title={t('docs.emptyTitle')} description={t('docs.emptyDescription')} />
        ) : (
          <div className="u-row u-wrap u-xs">
            <span>
              {t('docs.specUrl')} <CopyableId value={specUrl} length={40} />
            </span>
            <span className="u-muted">
              {t('docs.operationCount', {
                paths: summary.paths,
                operations: summary.operations,
              })}
            </span>
            <span className="u-muted">
              {t('docs.language')} <span className="u-mono">{language}</span>
            </span>
          </div>
        )}
      </Card>

      {assets === 'loading' ? (
        <Card>
          <div className="u-stack-sm" aria-busy="true">
            <Skeleton height={18} width="30%" />
            <Skeleton height={44} />
            <Skeleton height={44} />
            <Skeleton height={44} />
          </div>
        </Card>
      ) : null}

      {assets === 'failed' ? (
        <Card>
          <ErrorState
            error={new Error(t('docs.assetsFailed'))}
            title={t('docs.assetsFailedTitle')}
            onRetry={retry}
          />
          <div className="u-row u-wrap" style={{ justifyContent: 'center' }}>
            <Button
              variant="secondary"
              icon={<ExternalIcon />}
              onClick={() => {
                openExternal(`${paths.docs.swagger}?lang=${language}`)
              }}
            >
              {t('docs.openSwagger')}
            </Button>
            <Button
              variant="ghost"
              icon={<ExternalIcon />}
              onClick={() => {
                openExternal(specUrl)
              }}
            >
              {t('docs.openSpec')}
            </Button>
            <Button variant="ghost" icon={<RefreshIcon />} onClick={retry}>
              {t('common:action.retry')}
            </Button>
          </div>
        </Card>
      ) : null}

      <div
        className="dtk-swagger"
        hidden={assets !== 'ready'}
        style={{
          background: 'var(--bg-raised)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          padding: 'var(--space-2)',
          overflowX: 'auto',
        }}
      >
        <div ref={container} />
      </div>
    </div>
  )
}
