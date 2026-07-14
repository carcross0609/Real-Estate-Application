"""Single import surface for every ORM model in the platform.

Importing this module guarantees `Base.metadata` is fully populated — needed by Alembic
autogenerate/offline render and by the metadata test suite. New modules add their import
here (and mirror it in `alembic/env.py`). Import order is irrelevant: cross-module foreign
keys are declared by string table name, resolved at mapper-configuration time once every
model class is imported.
"""

from deallens.modules.admin import models as admin_models
from deallens.modules.alerts import models as alerts_models
from deallens.modules.engine import models as engine_models
from deallens.modules.enrichment import models as enrichment_models
from deallens.modules.identity import models as identity_models
from deallens.modules.ingestion import models as ingestion_models
from deallens.modules.markets import models as markets_models
from deallens.modules.reports import models as reports_models
from deallens.modules.scoring import models as scoring_models
from deallens.modules.search import models as search_models
from deallens.modules.vision import models as vision_models

__all__ = [
    "admin_models",
    "alerts_models",
    "engine_models",
    "enrichment_models",
    "identity_models",
    "ingestion_models",
    "markets_models",
    "reports_models",
    "scoring_models",
    "search_models",
    "vision_models",
]
