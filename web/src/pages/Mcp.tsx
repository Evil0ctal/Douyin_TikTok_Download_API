import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Banner,
  Card,
  CodeBlock,
  Disclosure,
  ExternalIcon,
  InfoIcon,
  PageHeader,
  Select,
} from '@/components'
import { useApiQuery } from '@/hooks'
import { paths } from '@/lib/endpoints'

import styles from './Mcp.module.css'

/**
 * How to point an agent at this instance.
 *
 * The page exists because the endpoint was undiscoverable. It is mounted, it
 * works, and nothing in the console mentioned it - so the only way to find out
 * this instance speaks MCP was to read the source or the OpenAPI document.
 *
 * Everything here is generated from the browser's own location rather than
 * typed as an example, because the two mistakes that cost the most time are
 * both address mistakes: pasting `localhost` into a client running on another
 * machine, and losing the trailing slash. `/mcp` answers a 307 to `/mcp/`, and
 * a client that does not re-POST on redirect fails with something that reads
 * like an auth error.
 */

/** The tool names, as `tools/list` returns them. Kept for the summary only. */
const TOOLS = [
  'parse_url',
  'get_video',
  'get_user',
  'list_user_posts',
  'list_comments',
  'get_content_history',
  'pool_status',
  'get_task_result',
] as const

type ClientId = 'claudeCode' | 'claudeDesktop' | 'codex' | 'cherry' | 'generic' | 'stdio'

const CLIENTS: readonly ClientId[] = [
  'claudeCode',
  'claudeDesktop',
  'codex',
  'cherry',
  'generic',
  'stdio',
]

/**
 * Which clients are configured with JSON, and so get the highlighter. The
 * others are a shell command, a TOML block and a raw request - all of which
 * `text` renders honestly and `json` would decorate with the wrong colours.
 */
const JSON_CONFIGURED: ReadonlySet<ClientId> = new Set(['claudeDesktop', 'cherry'])

function snippetFor(client: ClientId, url: string, key: string): string {
  switch (client) {
    case 'claudeCode':
      return [
        `claude mcp add --transport http dtk ${url} \\`,
        `  --header "Authorization: Bearer ${key}"`,
      ].join('\n')
    case 'claudeDesktop':
      // Not `type: 'http'`: claude_desktop_config.json validates stdio servers
      // only, and Claude Desktop's own remote-connector UI authenticates with
      // OAuth, which this server does not speak. `mcp-remote` is the bridge
      // that does - it is a stdio process that forwards to a streamable-http
      // endpoint and can carry a static header.
      //
      // The key travels in `env` and the header value carries no space, on
      // purpose: mcp-remote documents that Claude Desktop on Windows (and
      // Cursor, and Codex CLI) fail to escape spaces inside `args`, which
      // mangles `"Authorization: Bearer ..."`. `X-API-Key:${VAR}` has nowhere
      // for that bug to bite, and this server accepts it as readily.
      return JSON.stringify(
        {
          mcpServers: {
            dtk: {
              command: 'npx',
              args: ['-y', 'mcp-remote', url, '--header', 'X-API-Key:${DTK_API_KEY}'],
              env: { DTK_API_KEY: key },
            },
          },
        },
        null,
        2,
      )
    case 'codex':
      // `bearer_token_env_var` rather than a literal in `http_headers`: it is
      // what Codex documents for this, and it keeps the key out of a file
      // people commit.
      return [
        '[mcp_servers.dtk]',
        `url = "${url}"`,
        'bearer_token_env_var = "DTK_API_KEY"',
      ].join('\n')
    case 'cherry':
      return JSON.stringify(
        {
          mcpServers: {
            // No `name`: Cherry Studio falls back to the key, and a second
            // place to spell it is a second place for it to go stale.
            dtk: {
              type: 'streamableHttp',
              url,
              headers: { Authorization: `Bearer ${key}` },
            },
          },
        },
        null,
        2,
      )
    case 'stdio':
      return [
        '# Runs on the same machine as the database, and reads the same',
        '# environment the API process does. No API key: there is no HTTP',
        '# request to authenticate.',
        'DTK_DATABASE_URL=... DTK_REDIS_URL=... DTK_SECRET_KEY=... \\',
        '  python -m dtk.mcp',
      ].join('\n')
    default:
      return [
        `POST ${url}`,
        'Authorization: Bearer ' + key,
        'Content-Type: application/json',
        'Accept: application/json, text/event-stream',
      ].join('\n')
  }
}

export default function Mcp() {
  const { t } = useTranslation(['console', 'common'])
  const [client, setClient] = useState<ClientId>('claudeCode')

  const keys = useApiQuery<Array<{ id: string; name: string; prefix: string }>>({
    key: ['admin', 'api-keys'],
    path: paths.apiKeys.list,
  })

  /**
   * The address as the browser reached it, with the trailing slash.
   *
   * `window.location.origin` rather than a placeholder: a config generated on
   * this page is usually pasted into a client on the same machine, and the one
   * thing that is definitely right is the URL that just loaded this page. It is
   * still wrong for an agent on a different host, which the note beside it
   * says.
   */
  const url = useMemo(
    () => (typeof window === 'undefined' ? '/mcp/' : `${window.location.origin}/mcp/`),
    [],
  )

  /**
   * Only claim there are no keys when the list actually came back empty.
   * While it is loading, or when the request failed - a session without the
   * admin role gets a 403 here - the honest answer is to say nothing rather
   * than to tell the reader something about their instance that is not true.
   */
  const noKeys = keys.isSuccess && keys.data.length === 0
  const placeholder = 'dtk_YOUR_API_KEY'
  const snippet = snippetFor(client, url, placeholder)

  return (
    <div className="u-page">
      <PageHeader
        title={t('console:mcp.title')}
        description={t('console:mcp.description')}
        badge={
          <span className="u-xs u-muted">
            {t('console:mcp.toolCount', { count: TOOLS.length })}
          </span>
        }
      />

      <Card title={t('console:mcp.endpoint.title')} description={t('console:mcp.endpoint.body')}>
        <CodeBlock code={url} language="text" />
        <div className={styles.facts}>
          <span>
            <strong>{t('console:mcp.endpoint.transport')}</strong> streamable-http
          </span>
          <span>
            <strong>{t('console:mcp.endpoint.auth')}</strong>{' '}
            {t('console:mcp.endpoint.authValue')}
          </span>
        </div>
        {/* The two mistakes that cost the most time, said before the snippets
            rather than after somebody has hit them. */}
        <ul className={styles.gotchas}>
          <li>{t('console:mcp.endpoint.slash')}</li>
          <li>{t('console:mcp.endpoint.host')}</li>
          <li>{t('console:mcp.endpoint.cookie')}</li>
        </ul>
      </Card>

      {noKeys ? (
        <Banner tone="caution" icon={<InfoIcon size={14} />}>
          {t('console:mcp.noKeys')}
        </Banner>
      ) : null}

      <Card title={t('console:mcp.configure.title')} description={t('console:mcp.configure.body')}>
        <Select
          label={t('console:mcp.configure.client')}
          value={client}
          onChange={(event) => {
            setClient(event.target.value as ClientId)
          }}
          fieldClassName={styles.clientPicker}
        >
          {CLIENTS.map((id) => (
            <option key={id} value={id}>
              {t(`console:mcp.client.${id}.name`)}
            </option>
          ))}
        </Select>

        <p className={styles.where}>{t(`console:mcp.client.${client}.where`)}</p>
        <CodeBlock code={snippet} language={JSON_CONFIGURED.has(client) ? 'json' : 'text'} />
        <p className="u-xs u-muted" style={{ margin: 'var(--space-3) 0 0' }}>
          {t('console:mcp.configure.keyNote')}
        </p>
      </Card>

      <Card title={t('console:mcp.tools.title')} description={t('console:mcp.tools.body')} flush>
        <ul className={styles.tools}>
          {TOOLS.map((name) => (
            <li key={name}>
              <code className="u-mono">{name}</code>
              <span className="u-secondary">{t(`console:mcp.tool.${name}`)}</span>
            </li>
          ))}
        </ul>
      </Card>

      <Card flush>
        <Disclosure title={t('console:mcp.usage.title')}>
          <div className="u-stack">
            <p className="u-secondary" style={{ margin: 0 }}>
              {t('console:mcp.usage.async')}
            </p>
            <p className="u-secondary" style={{ margin: 0 }}>
              {t('console:mcp.usage.scopes')}
            </p>
            <p className="u-secondary" style={{ margin: 0 }}>
              {t('console:mcp.usage.paging')}
            </p>
            <a
              className={styles.docsLink}
              href={`${paths.docs.swagger}`}
              target="_blank"
              rel="noreferrer noopener"
            >
              <ExternalIcon size={12} />
              {t('console:mcp.usage.rest')}
            </a>
          </div>
        </Disclosure>
      </Card>
    </div>
  )
}
