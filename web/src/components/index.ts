/**
 * Component inventory (docs/design/12-design-system.md).
 *
 * Page modules should import from '@/components' rather than reaching into
 * individual files, so a component can be reshaped without touching pages.
 */

export { Button } from './Button'
export type { ButtonProps, ButtonSize, ButtonVariant } from './Button'

export { Card } from './Card'
export type { CardProps } from './Card'

export { Checkbox } from './Checkbox'
export type { CheckboxProps } from './Checkbox'

export { CodeBlock } from './CodeBlock'
export type { CodeBlockProps } from './CodeBlock'

export { ConfirmDialog } from './ConfirmDialog'
export type { ConfirmDialogProps } from './ConfirmDialog'

export { CopyableId } from './CopyableId'
export type { CopyableIdProps } from './CopyableId'

export { DataTable } from './DataTable'
export type { Column, DataTableProps, Density, SortState } from './DataTable'

export { Disclosure } from './Disclosure'
export type { DisclosureProps } from './Disclosure'

export { Drawer } from './Drawer'
export type { DrawerProps } from './Drawer'

export { EmptyState } from './EmptyState'
export type { EmptyStateProps } from './EmptyState'

export { ErrorBoundary } from './ErrorBoundary'
export { ErrorState, useErrorInfo } from './ErrorState'
export type { ErrorInfo, ErrorStateProps } from './ErrorState'

export { Field, describedBy } from './Field'
export type { FieldProps } from './Field'

export * from './Icons'

export { Input } from './Input'
export { Logo } from './Logo'
export { Sponsor, SPONSOR } from './Sponsor'
export type { InputProps } from './Input'

export { LanguageSwitcher } from './LanguageSwitcher'

export { MaskedSecret } from './MaskedSecret'
export type { MaskedSecretProps } from './MaskedSecret'

export { Menu, MenuItem, MenuSection } from './Menu'
export type { MenuItemProps, MenuProps } from './Menu'

export { MetricTile } from './MetricTile'
export type { MetricTileProps } from './MetricTile'

export { Modal } from './Modal'
export type { ModalProps, ModalSize } from './Modal'

export { PageHeader } from './PageHeader'
export type { PageHeaderProps } from './PageHeader'

export { Select } from './Select'
export type { SelectOption, SelectProps } from './Select'

export { Sidebar, isActivePath } from './Sidebar'
export type { SidebarProps } from './Sidebar'

export { Skeleton, SkeletonText } from './Skeleton'
export type { SkeletonProps, SkeletonTextProps } from './Skeleton'

export { SettingEditor, Banner, sourceOf } from './SettingEditor'
export type { SettingRow, SourceKind, RowProps as SettingEditorProps } from './SettingEditor'
export { SigningStages } from './SigningStages'
export type { SigningStage, SigningStagesProps } from './SigningStages'
export { SplitPane } from './SplitPane'
export type { SplitPaneProps } from './SplitPane'

export { ErrorCodeBadge, StatusBadge, toneClass } from './StatusBadge'
export type { ErrorCodeBadgeProps, StatusBadgeProps } from './StatusBadge'

export { Switch } from './Switch'
export type { SwitchProps } from './Switch'

export { Textarea } from './Textarea'
export type { TextareaProps } from './Textarea'

export { ThemeToggle } from './ThemeToggle'

export { TimeSeriesChart } from './TimeSeriesChart'
export type { ChartPoint, ChartSeries, TimeSeriesChartProps } from './TimeSeriesChart'

export { ToastProvider, useToast } from './Toast'
export type { ToastApi, ToastOptions, ToastTone } from './Toast'

export { TopBar } from './TopBar'
export type { TopBarProps } from './TopBar'
