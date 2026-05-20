# Central model registry — import ALL models here so that:
# 1. Alembic's env.py gets the full metadata for autogenerate
# 2. SQLAlchemy knows all tables before creating the engine
#
# Rule: every new model file MUST be imported here.

from app.db.base import Base  # noqa: F401  — re-export Base for env.py

# ── Feature models ─────────────────────────────────────────────────────────
from app.features.users.models import User  # noqa: F401
from app.features.cases.models import (  # noqa: F401
    Case,
    CaseNumber,
    CaseSection,
    CaseAccess,
    Party,
)
from app.features.documents.models import Document  # noqa: F401
from app.features.extraction.models import (  # noqa: F401
    ExtractionResult,
    TypedVersion,
)
from app.features.context.models import (  # noqa: F401
    CaseContext,
    CaseContextVersion,
    CaseSummary,
)
from app.features.drafts.models import Draft, DraftExport  # noqa: F401
from app.features.notifications.models import Notification  # noqa: F401
from app.features.cause_listings.models import CauseListing  # noqa: F401
from app.features.courtroom.models import CourtroomSession  # noqa: F401

__all__ = ["Base"]
