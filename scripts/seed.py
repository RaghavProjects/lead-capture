"""Initialize the database, seed the business profile and import sample leads.

Usage:
    python -m scripts.seed            # reset + seed
    python -m scripts.seed --keep     # seed only if empty
"""
from __future__ import annotations

import sys
from pathlib import Path

from config import settings
from data.repository import SQLiteRepository
from models.schemas import BusinessProfile
from services.importer import import_leads
from services.leads import LeadService

SAMPLE_CSV = Path(__file__).resolve().parent.parent / "sample_data" / "sample_leads.csv"


def seed(database_url: str | None = None, reset: bool = True) -> dict:
    repo = SQLiteRepository(database_url or settings.db_path)
    service = LeadService(repo)
    if reset:
        service.reset_demo_data()
    repo.save_business_profile(BusinessProfile())

    report = import_leads(str(SAMPLE_CSV), repo, filename="sample_leads.csv")
    return {
        "imported": report.imported,
        "failed": report.failed,
        "errors": [e.message for e in report.errors],
        "total_leads": repo.count_leads(),
    }


def main() -> None:
    reset = "--keep" not in sys.argv
    result = seed(reset=reset)
    print(f"Seeded {result['imported']} leads ({result['failed']} rejected). Total: {result['total_leads']}")
    for message in result["errors"]:
        print("  -", message)


if __name__ == "__main__":
    main()
