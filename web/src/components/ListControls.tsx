import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from './Button'
import { Input } from './Input'

/**
 * Search and paging for an offset-paged list: Downloads and Transcriptions.
 *
 * Both lists are read 100 rows at a time, and both pages used to ask for the
 * first hundred and stop - no offset was ever sent and the `total` the API
 * returned was dropped, so the 101st download could not be reached from the
 * console at all, and nothing on screen said there was one.
 */

export interface SearchBoxProps {
  /** The search being applied, as the URL holds it. */
  value: string
  onSearch: (value: string) => void
  label: string
  placeholder?: string
}

/**
 * A search box that applies on Enter, not on every keystroke.
 *
 * Typing is not a decision until it stops, and a query per character would
 * page the table on the way to a word - the Library's reasoning, kept. The
 * box follows the applied value when a link changes it, so arriving from
 * another page never shows one search and filters by another.
 */
export function SearchBox({ value, onSearch, label, placeholder }: SearchBoxProps) {
  const { t } = useTranslation('common')
  const [draft, setDraft] = useState(value)
  const [seen, setSeen] = useState(value)
  if (seen !== value) {
    setSeen(value)
    setDraft(value)
  }

  return (
    <form
      className="u-row"
      role="search"
      onSubmit={(event: FormEvent) => {
        event.preventDefault()
        onSearch(draft.trim())
      }}
    >
      <Input
        type="search"
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value)
          // Clearing the box is a decision too: the native clear button and a
          // select-all-delete both leave nothing to submit.
          if (event.target.value === '' && value !== '') onSearch('')
        }}
        aria-label={label}
        placeholder={placeholder}
      />
      <Button type="submit" size="sm" variant="secondary">
        {t('action.search')}
      </Button>
    </form>
  )
}

export interface PagerProps {
  offset: number
  pageSize: number
  /** Every match, not the page: what the API returns as `total`. */
  total: number
  onOffset: (offset: number) => void
  /** "101-200 of 340", rendered by the page in its own words. */
  range: (from: number, to: number, total: number) => string
  /**
   * The two buttons' words. The shared "next" is a wizard's "next step" in
   * Chinese, which read wrongly beside "previous page".
   */
  labels: { previous: string; next: string }
}

/**
 * Where the last page starts, for a list of `total` rows.
 *
 * For stepping back when the page being shown no longer exists: its last rows
 * deleted, or deduplicated away, left an empty page that showed the empty
 * state of a list that was not empty, under "101-100 of 100".
 */
export function lastPageOffset(total: number, pageSize: number): number {
  return Math.max(0, Math.floor((total - 1) / pageSize) * pageSize)
}

/** Previous, where this page sits in the whole, and Next. Hidden for one page. */
export function Pager({ offset, pageSize, total, onOffset, range, labels }: PagerProps) {
  if (total <= pageSize && offset === 0) return null
  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + pageSize, total)
  return (
    <div className="u-row-between">
      <Button
        variant="secondary"
        disabled={offset === 0}
        onClick={() => {
          onOffset(Math.max(0, offset - pageSize))
        }}
      >
        {labels.previous}
      </Button>
      <span className="u-xs u-muted">{range(from, to, total)}</span>
      <Button
        variant="secondary"
        disabled={offset + pageSize >= total}
        onClick={() => {
          onOffset(offset + pageSize)
        }}
      >
        {labels.next}
      </Button>
    </div>
  )
}
