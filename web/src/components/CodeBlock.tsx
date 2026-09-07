import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'
import { useCopy } from '@/hooks/useCopy'

import { Button } from './Button'
import { CheckIcon, CopyIcon } from './Icons'
import styles from './data.module.css'

/** Matches strings (with an optional following colon, which makes them keys), literals and numbers. */
const JSON_TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g

function highlight(source: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let lastIndex = 0
  let key = 0

  for (const match of source.matchAll(JSON_TOKEN)) {
    const index = match.index ?? 0
    if (index > lastIndex) {
      nodes.push(<span key={key++}>{source.slice(lastIndex, index)}</span>)
    }

    const [full, string, colon, literal, numeric] = match
    if (string !== undefined) {
      nodes.push(
        <span key={key++} className={colon ? styles.jsonKey : styles.jsonString}>
          {string}
        </span>,
      )
      if (colon) {
        nodes.push(
          <span key={key++} className={styles.jsonPunct}>
            {colon}
          </span>,
        )
      }
    } else if (literal !== undefined) {
      nodes.push(
        <span key={key++} className={literal === 'null' ? styles.jsonNull : styles.jsonBoolean}>
          {literal}
        </span>,
      )
    } else if (numeric !== undefined) {
      nodes.push(
        <span key={key++} className={styles.jsonNumber}>
          {numeric}
        </span>,
      )
    }
    lastIndex = index + full.length
  }

  if (lastIndex < source.length) {
    nodes.push(<span key={key++}>{source.slice(lastIndex)}</span>)
  }
  return nodes
}

export interface CodeBlockProps {
  /** Raw text. Ignored when `json` is given. */
  code?: string
  /** Any value; serialized with two-space indent. */
  json?: unknown
  title?: ReactNode
  language?: 'json' | 'text'
  collapsible?: boolean
  defaultCollapsed?: boolean
  copyable?: boolean
  className?: string
}

/**
 * Raw responses live here. The playground shows the normalized result beside the
 * platform payload, and that payload is the evidence when a parser breaks, so it
 * has to be readable and copyable in one click (docs/design/07-frontend.md).
 */
export function CodeBlock({
  code,
  json,
  title,
  language = 'json',
  collapsible = true,
  defaultCollapsed = false,
  copyable = true,
  className,
}: CodeBlockProps) {
  const { t } = useTranslation()
  const { copied, copy } = useCopy()
  const [collapsed, setCollapsed] = useState(defaultCollapsed)

  const text = useMemo(() => {
    if (json !== undefined) {
      try {
        return JSON.stringify(json, null, 2)
      } catch {
        return String(json)
      }
    }
    return code ?? ''
  }, [code, json])

  const lineCount = useMemo(() => text.split('\n').length, [text])
  const content = useMemo(
    () => (language === 'json' ? highlight(text) : text),
    [language, text],
  )

  return (
    <div className={cn(styles.codeBlock, className)}>
      {title || copyable || collapsible ? (
        <div className={styles.codeHeader}>
          <span className="u-truncate">{title ?? t('code.lines', { count: lineCount })}</span>
          <span className={styles.codeActions}>
            {collapsible && lineCount > 8 ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setCollapsed((value) => !value)
                }}
              >
                {collapsed ? t('code.expand') : t('code.collapse')}
              </Button>
            ) : null}
            {copyable ? (
              <Button
                size="sm"
                variant="ghost"
                icon={copied ? <CheckIcon /> : <CopyIcon />}
                aria-label={t('code.copy')}
                onClick={() => void copy(text)}
              />
            ) : null}
          </span>
        </div>
      ) : null}
      <pre className={cn(styles.codeScroll, collapsed && styles.codeCollapsed)}>
        <code>{content}</code>
      </pre>
    </div>
  )
}
