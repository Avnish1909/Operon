"""Company knowledge retrieval, cross-run lessons, and the shared-drive tools."""

from __future__ import annotations

import pytest

from operon.knowledge import KnowledgeBase, LessonStore
from operon.tools.base import ToolError
from operon.tools.files import SharedDrive


@pytest.fixture(scope="module")
def kb(company_dir_module):
    return KnowledgeBase(company_dir_module, {"erp": "http://x/erp/", "desk": "http://x/desk/", "hr": "http://x/hr/"})


@pytest.fixture(scope="module")
def company_dir_module():
    from operon.config import Settings

    return Settings().company_dir


@pytest.mark.parametrize("query, expected", [
    ("Ticket 1042 — the customer says they were charged twice", "sop-refunds"),
    ("Please process the vendor invoices in the AP inbox", "sop-accounts-payable"),
    ("Globex wants us to delete their account and all their data", "policy-data-protection"),
    ("new hire joining as a Sales Associate, get her set up", "sop-onboarding"),
    ("customers more than 60 days overdue for collections", "sop-collections"),
])
def test_search_finds_the_right_procedure(kb, query, expected):
    assert kb.search(query, k=1)[0]["doc_id"] == expected


def test_system_urls_are_filled_into_documents(kb):
    assert "http://x/erp/" in kb.read("systems-guide").body


def test_unknown_document_suggests_alternatives(kb):
    with pytest.raises(KeyError, match="refunds"):
        kb.read("refunds")


def test_lessons_dedupe_and_rank(tmp_path):
    store = LessonStore(tmp_path / "lessons.jsonl")
    assert store.add("erp", "The bill form needs invoice dates as DD/MM/YYYY.", "r1")
    assert not store.add("erp", "the bill form needs invoice dates as  DD/MM/YYYY.", "r2")
    store.add("hr", "Start date uses the ISO date picker.", "r3")
    assert store.relevant("enter vendor bill invoice date")[0]["system"] == "erp"


def test_drive_confines_paths(world, settings):
    drive = SharedDrive(settings.workspace_dir)
    with pytest.raises(ToolError, match="outside"):
        drive.read("../vault.json")
    with pytest.raises(ToolError, match="outside"):
        drive.list("..\\..")


def test_drive_reads_pdfs_and_spreadsheets(world, settings):
    drive = SharedDrive(settings.workspace_dir)
    pdf = drive.read("ap_inbox/ACME-88213.pdf").text
    assert "PO-7781" in pdf and "$4,250.00" in pdf
    xlsx = drive.read("hr/role_access_matrix.xlsx").text
    assert "Sales Associate | Sales | yes | sales-user | viewer" in xlsx


def test_drive_write_and_move(world, settings):
    drive = SharedDrive(settings.workspace_dir)
    drive.write("reports/a.csv", "x,y\n1,2\n")
    with pytest.raises(ToolError, match="different number of columns"):
        drive.write("reports/b.csv", "x,y\n1,2,3\n")
    with pytest.raises(ToolError, match="already exists"):
        drive.write("reports/a.csv", "x\n")
    drive.move("ap_inbox/scan_0091.pdf", "ap_exceptions/")
    assert (settings.workspace_dir / "ap_exceptions" / "scan_0091.pdf").exists()
    with pytest.raises(ToolError, match="read-only"):
        SharedDrive(settings.workspace_dir, read_only=True).write("reports/c.csv", "x\n")
