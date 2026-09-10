import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from './Button'
import { Input } from './Input'
import { Modal } from './Modal'

export interface ConfirmDialogProps {
  open: boolean
  onCancel: () => void
  onConfirm: () => void
  title?: ReactNode
  description?: ReactNode
  confirmLabel?: ReactNode
  cancelLabel?: ReactNode
  danger?: boolean
  loading?: boolean
  /**
   * When set, the user must type this exact text to enable the confirm button.
   * Used for SENSITIVE settings and anything that destroys data
   * (docs/design/10-configuration.md, docs/design/15-operations.md).
   */
  confirmPhrase?: string
  children?: ReactNode
}

export function ConfirmDialog({
  open,
  onCancel,
  onConfirm,
  title,
  description,
  confirmLabel,
  cancelLabel,
  danger = false,
  loading = false,
  confirmPhrase,
  children,
}: ConfirmDialogProps) {
  const { t } = useTranslation()
  const [typed, setTyped] = useState('')

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the typed confirmation resets when the dialog closes, so reopening starts blank
    if (!open) setTyped('')
  }, [open])

  const blocked = Boolean(confirmPhrase) && typed.trim() !== confirmPhrase

  return (
    <Modal
      open={open}
      onClose={onCancel}
      size="sm"
      title={title ?? t('dialog.confirmTitle')}
      description={description}
      closeOnBackdrop={!loading}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} disabled={loading}>
            {cancelLabel ?? t('action.cancel')}
          </Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            onClick={onConfirm}
            loading={loading}
            disabled={blocked}
          >
            {confirmLabel ?? t('action.confirm')}
          </Button>
        </>
      }
    >
      {children}
      {confirmPhrase ? (
        <Input
          label={t('dialog.typeToConfirm', { phrase: confirmPhrase })}
          value={typed}
          mono
          autoComplete="off"
          onChange={(event) => {
            setTyped(event.target.value)
          }}
        />
      ) : null}
    </Modal>
  )
}
