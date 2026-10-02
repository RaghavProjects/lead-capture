import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import Settings  # noqa: E402
from data.repository import SQLiteRepository  # noqa: E402
from services.ai import MockAdapter  # noqa: E402
from services.leads import LeadService  # noqa: E402

SAMPLE_CSV = PROJECT_ROOT / "sample_data" / "sample_leads.csv"
SAMPLE_XLSX = PROJECT_ROOT / "sample_data" / "sample_leads.xlsx"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        ai_provider="mock",
        ai_model="test-model",
        ai_api_key="",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        app_env="test",
    )


@pytest.fixture
def repo(tmp_path):
    return SQLiteRepository(str(tmp_path / "test.db"))


@pytest.fixture
def service(repo, settings):
    return LeadService(repo, adapter=MockAdapter(), app_settings=settings)
