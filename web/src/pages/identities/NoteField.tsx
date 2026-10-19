import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Input, useToast } from '@/components'
import { useApiMutation, useFormatters } from '@/hooks'
import { apiPatch } from '@/lib/api'
import { paths } from '@/lib/endpoints'

import type { IdentityRow } from './types'

/** Mirrors IDENTITY_NOTE_MAX in src/dtk/db/models.py. */
export const NOTE_MAX = 200

/**
 * The note an operator keeps with an identity, and the account the platform
 * last said it belongs to.
 *
 * Both, because they answer the same question from two sides: the note is
 * what somebody believes - "alice's login, from the work laptop" - and the
 * account id is what the platform answered when the session was checked, so a
 * stale note or one login imported twice still shows.
 */
export function NoteField({
  identity,
  canManage,
  onChanged,
}: {
  identity: IdentityRow
  canManage: boolean
  onChanged: () => Promise<void>
}) {
  const { t } = useTranslation(['console', 'common'])
  const format = useFormatters()
  const toast = useToast()
  const [draft, setDraft] = useState<string | null>(null)

  const save = useApiMutation<IdentityRow, string | null>(
    (note) => apiPatch<IdentityRow>(paths.identities.byId(identity.id), { note }),
    {
      onSuccess: async () => {
        setDraft(null)
        toast.success(t('console:identity.note.saved'))
        await onChanged()
      },
      onError: (error) => {
        toast.apiError(error, t('console:identity.note.failed'))
      },
    },
  )

  const commit = () => {
    if (draft === null) return
    const note = draft.trim()
    if (note === (identity.note ?? '')) {
      setDraft(null)
      return
    }
    save.mutate(note === '' ? null : note)
  }

  return (
    <div className="u-stack-sm">
      {draft !== null ? (
        <div className="u-stack-sm">
          <Input
            label={t('console:identity.note.label')}
            value={draft}
            maxLength={NOTE_MAX}
            autoFocus
            disabled={save.isPending}
            placeholder={t('console:identity.note.placeholder')}
            description={t('console:identity.note.count', {
              used: draft.trim().length,
              max: NOTE_MAX,
            })}
            onChange={(event) => {
              setDraft(event.target.value.replace(/[\r\n]+/g, ' '))
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                commit()
              } else if (event.key === 'Escape') {
                // Kept from the drawer, which would otherwise close on it and
                // take the half-written note with it.
                event.stopPropagation()
                setDraft(null)
              }
            }}
          />
          <span className="u-row u-wrap">
            <Button
              size="sm"
              variant="ghost"
              disabled={save.isPending}
              onClick={() => {
                setDraft(null)
              }}
            >
              {t('common:action.cancel')}
            </Button>
            <Button size="sm" variant="primary" loading={save.isPending} onClick={commit}>
              {t('common:action.save')}
            </Button>
          </span>
        </div>
      ) : (
        <div className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
          <span className="u-xs u-muted">{t('console:identity.note.label')}</span>
          <span className={identity.note ? undefined : 'u-muted'} style={{ wordBreak: 'break-word' }}>
            {identity.note ?? t('console:identity.note.none')}
          </span>
          {canManage ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setDraft(identity.note ?? '')
              }}
            >
              {identity.note ? t('common:action.edit') : t('console:identity.note.add')}
            </Button>
          ) : null}
        </div>
      )}

      {identity.account_id ? (
        <div className="u-row u-wrap" style={{ gap: 'var(--space-2)' }}>
          <span className="u-xs u-muted" title={t('console:identity.account.hint')}>
            {t('console:identity.account.label')}
          </span>
          <span className="u-mono">{identity.account_id}</span>
          {identity.account_seen_at ? (
            <span className="u-xs u-muted" title={format.timestamp(identity.account_seen_at)}>
              {t('console:identity.account.seen', {
                when: format.relative(identity.account_seen_at),
              })}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
