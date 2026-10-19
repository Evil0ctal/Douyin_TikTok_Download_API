/**
 * Picking one part of a Bilibili video uploaded in several.
 *
 * A plain download stores the first part, the one the post's link plays. The
 * others are reachable by number, and a number alone is a poor thing to ask
 * someone for - so the picker lists them, by title and length, on request.
 * On request rather than as the link is typed: listing costs an identity and
 * a real request, and most videos have one part.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Select } from '@/components'
import { useFormatters } from '@/hooks'
import { apiGet } from '@/lib/api'
import { API_V1 } from '@/lib/endpoints'

import styles from './Downloads.module.css'

export interface VideoPart {
  part: number
  cid: string
  title: string
  duration_ms: number | null
}

/** Whether a pasted link is a Bilibili video page, whose `?p=` picks a part. */
export const BILIBILI_LINK = /(^|[\s/.])bilibili\.com\//i

/**
 * The part a link plays: its `?p=`, which the API reads the same way.
 *
 * Read here too so the picker starts on it. A link that says part 3 and a
 * picker left on "first part" would send nothing, and the API would store part
 * 3 while the form said otherwise.
 */
export function partFromLink(text: string): number | null {
  const match = /[?&]p=(\d+)/.exec(text)
  const part = match ? Number(match[1]) : NaN
  return Number.isInteger(part) && part > 0 ? part : null
}

export function PartPicker({
  query,
  value,
  onChange,
}: {
  /** How to name the video to the parts endpoint: `aweme_id` or `url`. */
  query: Record<string, string>
  /** The chosen part as text, empty for the first. */
  value: string
  onChange: (value: string) => void
}) {
  const { t } = useTranslation('console')
  const formatters = useFormatters()
  const [parts, setParts] = useState<VideoPart[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)

  const load = async (): Promise<void> => {
    setLoading(true)
    setFailed(false)
    try {
      const page = await apiGet<{ items: VideoPart[] }>(`${API_V1}/bilibili/video/parts`, {
        params: query,
      })
      setParts(page.items)
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }

  const chosen = Number(value)
  const options = [
    { value: '', label: t('downloads.part.first') },
    ...(parts ?? [])
      .filter((part) => part.part > 1)
      .map((part) => ({
        value: String(part.part),
        label: t('downloads.part.option', {
          part: part.part,
          title: part.title,
          duration: formatters.duration(part.duration_ms),
        }),
      })),
  ]
  // A part named by the link before the list is loaded still has to be shown
  // as the selection, or the form would claim the first part.
  if (chosen > 1 && !options.some((option) => option.value === value)) {
    options.push({ value, label: t('downloads.part.numbered', { part: chosen }) })
  }

  return (
    <div className={styles.partPicker}>
      <Select
        label={t('downloads.part.label')}
        description={
          failed
            ? t('downloads.part.failed')
            : parts?.length === 1
              ? t('downloads.part.single')
              : t('downloads.part.hint')
        }
        value={value}
        options={options}
        onChange={(event) => {
          onChange(event.target.value)
        }}
        fieldClassName={styles.partField}
      />
      <Button
        size="sm"
        variant="secondary"
        loading={loading}
        disabled={parts !== null}
        onClick={() => {
          void load()
        }}
      >
        {parts === null ? t('downloads.part.load') : t('downloads.part.loaded', { count: parts.length })}
      </Button>
    </div>
  )
}
