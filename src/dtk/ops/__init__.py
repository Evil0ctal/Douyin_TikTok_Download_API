"""Operational features: health, backup, notifications, diagnostics, retention.

None of these appear in the architecture diagram, and every one of them turns
into a pile of issues when it is missing (docs/design/15-operations.md). They
are grouped here because they share one property: they are what a self-hosting
user needs in order to run, fix and move the instance themselves.
"""

from dtk.ops.backup import (
    BackupError,
    BackupInfo,
    Manifest,
    RestoreReport,
    SecretKeyMismatch,
    create_backup,
    list_backups,
    restore_backup,
)
from dtk.ops.channels import (
    Channel,
    ChannelType,
    build_channel,
    build_channels,
    validate_outbound_url,
)
from dtk.ops.diagnose import (
    DiagnoseContext,
    DiagnosticReport,
    StepResult,
    StepStatus,
    redact,
    run_diagnostics,
)
from dtk.ops.health import (
    ComponentHealth,
    LivenessReport,
    ReadinessReport,
    StatusReport,
    liveness,
    readiness,
    system_status,
)
from dtk.ops.notify import (
    Delivery,
    Notifier,
    NotifyEvent,
    Severity,
    notifier_from_config,
)
from dtk.ops.retention import (
    RetentionReport,
    apply_retention,
    blank_expired_task_payloads,
    run_maintenance,
)

__all__ = [
    "BackupError",
    "BackupInfo",
    "Channel",
    "ChannelType",
    "ComponentHealth",
    "Delivery",
    "DiagnoseContext",
    "DiagnosticReport",
    "LivenessReport",
    "Manifest",
    "Notifier",
    "NotifyEvent",
    "ReadinessReport",
    "RestoreReport",
    "RetentionReport",
    "SecretKeyMismatch",
    "Severity",
    "StatusReport",
    "StepResult",
    "StepStatus",
    "apply_retention",
    "blank_expired_task_payloads",
    "build_channel",
    "build_channels",
    "create_backup",
    "list_backups",
    "liveness",
    "notifier_from_config",
    "readiness",
    "redact",
    "restore_backup",
    "run_diagnostics",
    "run_maintenance",
    "system_status",
    "validate_outbound_url",
]
