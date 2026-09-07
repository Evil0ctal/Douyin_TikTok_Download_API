import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/cn'
import { useCopy } from '@/hooks/useCopy'

import { Button } from './Button'
import { CheckIcon, CopyIcon, LockIcon } from './Icons'
import styles from './data.module.css'

export interface MaskedSecretProps {
  /** Already masked by the server. No endpoint returns cookies or key secrets in clear. */
  value: string | null | undefined
  /** True only for a freshly created key, shown once and copyable. */
  oneTime?: boolean
  copyable?: boolean
  className?: string
}

/**
 * Cookie and API key display.
 *
 * The console never receives the plaintext, and the UI says so instead of
 * offering an eye icon that cannot work. The single exception is a key at
 * creation time, which is shown once and never again.
 */
export function MaskedSecret({ value, oneTime = false, copyable = false, className }: MaskedSecretProps) {
  const { t } = useTranslation()
  const { copied, copy } = useCopy()

  if (!value) return <span className="u-muted">—</span>

  return (
    <span className={cn(styles.secret, oneTime && styles.secretOnce, className)}>
      <LockIcon size={12} className={styles.secretLock} />
      <span className={styles.secretValue} title={oneTime ? undefined : t('secret.cannotReveal')}>
        {value}
      </span>
      {copyable ? (
        <Button
          size="sm"
          variant="ghost"
          icon={copied ? <CheckIcon /> : <CopyIcon />}
          aria-label={t('action.copy')}
          onClick={() => void copy(value)}
        />
      ) : null}
    </span>
  )
}
