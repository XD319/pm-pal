"""Product-decision domain: durable evidence and human decisions."""

from .models import (
    DecisionAuditEvent,
    DecisionInsight,
    EvidenceRecord,
    EvidenceSource,
    EvidenceSourceType,
    OpportunityCandidate,
    OpportunityCandidateStatus,
    SourceSyncStatus,
    SyncTrigger,
    WriteReceipt,
)
from .repository import ProductDecisionRepository
from .scheduler import DailyEvidenceSyncScheduler, next_shanghai_0200
from .services import (
    CollectService,
    DecisionDomainError,
    EvaluateService,
    InsightService,
    OpportunityService,
)
from .sync_service import EvidenceSyncService, shanghai_day_key, sync_idempotency_key

__all__ = [
    "CollectService",
    "DailyEvidenceSyncScheduler",
    "DecisionAuditEvent",
    "DecisionDomainError",
    "DecisionInsight",
    "EvaluateService",
    "EvidenceRecord",
    "EvidenceSource",
    "EvidenceSourceType",
    "EvidenceSyncService",
    "InsightService",
    "OpportunityCandidate",
    "OpportunityCandidateStatus",
    "OpportunityService",
    "ProductDecisionRepository",
    "SourceSyncStatus",
    "SyncTrigger",
    "WriteReceipt",
    "next_shanghai_0200",
    "shanghai_day_key",
    "sync_idempotency_key",
]
