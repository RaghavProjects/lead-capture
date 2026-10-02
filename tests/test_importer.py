from tests.conftest import SAMPLE_CSV, SAMPLE_XLSX
from services.importer import import_leads


def test_import_sample_csv_imports_all_30(repo):
    report = import_leads(str(SAMPLE_CSV), repo, filename="sample_leads.csv")
    assert report.imported == 30
    assert report.failed == 0
    assert report.missing_columns == []
    assert repo.count_leads() == 30
    assert len(set(report.lead_ids)) == 30


def test_import_xlsx_with_excel_dates(repo):
    report = import_leads(str(SAMPLE_XLSX), repo, filename="sample_leads.xlsx")
    assert report.imported == 30
    assert repo.count_leads() == 30
    lead = repo.get_lead("L-1001")
    assert lead is not None
    assert lead.received_at.year == 2026


def test_invalid_row_rejected_without_losing_valid_rows(repo, tmp_path):
    csv_path = tmp_path / "mixed.csv"
    csv_path.write_text(
        "Lead ID,Name,Inquiry,Lead Stage,Date Received,Response Status\n"
        "L-A,Valid Person,We need help with leads,New,2026-10-01,Not Contacted\n"
        "L-B,Broken Person,,New,2026-10-01,Not Contacted\n",
        encoding="utf-8",
    )
    report = import_leads(str(csv_path), repo, filename="mixed.csv")
    assert report.imported == 1
    assert report.failed == 1
    assert "Inquiry" in report.errors[0].message
    assert repo.get_lead("L-A") is not None
    assert repo.get_lead("L-B") is None


def test_missing_required_column_is_reported(repo, tmp_path):
    csv_path = tmp_path / "nocontent.csv"
    csv_path.write_text(
        "Name,Lead Stage,Date Received\nSomeone,New,2026-10-01\n",
        encoding="utf-8",
    )
    report = import_leads(str(csv_path), repo, filename="nocontent.csv")
    assert report.imported == 0
    assert "Inquiry" in report.missing_columns
    assert repo.count_leads() == 0


def test_duplicate_lead_id_rejected(repo, tmp_path):
    csv_path = tmp_path / "dupes.csv"
    csv_path.write_text(
        "Lead ID,Name,Inquiry,Lead Stage,Date Received,Response Status\n"
        "L-X,First,Help with leads,New,2026-10-01,Not Contacted\n"
        "L-X,Second,Help with leads too,New,2026-10-01,Not Contacted\n",
        encoding="utf-8",
    )
    report = import_leads(str(csv_path), repo, filename="dupes.csv")
    assert report.imported == 1
    assert report.failed == 1
    assert "Duplicate" in report.errors[0].message


def test_columns_can_be_mapped_by_synonym(repo, tmp_path):
    csv_path = tmp_path / "synonyms.csv"
    csv_path.write_text(
        "Name,Message,Stage,Received,Status\n"
        "Alias Person,Please tell me about your services,New,2026-10-01,Not Contacted\n",
        encoding="utf-8",
    )
    report = import_leads(str(csv_path), repo, filename="synonyms.csv")
    assert report.imported == 1