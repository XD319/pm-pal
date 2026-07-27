"""Quality-engine boundary for product decision artifacts.

This package is deliberately independent from HTTP, Feishu and product-workflow
orchestration.  Legacy review entrypoints remain available through
``prd_pal.service.review_service``.
"""

from .facade import QualityEngine
from .models import (
    QualityAssessment,
    QualityAssessmentRequest,
    QualityGateDecision,
)

__all__ = [
    "QualityAssessment",
    "QualityAssessmentRequest",
    "QualityEngine",
    "QualityGateDecision",
]
