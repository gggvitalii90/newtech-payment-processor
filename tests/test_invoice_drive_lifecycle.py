from datetime import date

from payment_processor.invoice_drive_lifecycle import (
    archive_paid_invoice,
    migrate_legacy_review_folder,
    resolve_payment_month_folder,
)
from payment_processor.invoice_archive import InvoiceArchiveRecord
from payment_processor.models import PaymentRecord


class Request:
    def __init__(self, value): self.value = value
    def execute(self): return self.value


class Drive:
    def __init__(self):
        self.children = {
            "root": [
                {"id": "obj", "name": "01_ ??? ??????", "mimeType": "application/vnd.google-apps.folder"},
                {"id": "review", "name": "\u041d\u0443\u0436\u043d\u043e \u0440\u0430\u0437\u043e\u0431\u0440\u0430\u0442\u044c", "mimeType": "application/vnd.google-apps.folder"},
                {"id": "unresolved", "name": "__", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "obj": [],
            "review": [{"id": "bad-file", "name": "bad.pdf", "mimeType": "application/pdf"}],
            "unresolved": [],
        }
        self.parents = {"bad-file": ["review"], "invoice-file": ["obj"]}
        self.updated = []
        self.created = []
        self.deny_trash = False

    def files(self): return self

    def list(self, q, fields, **kwargs):
        parent = q.split("'")[1]
        items = self.children.get(parent, [])
        if "mimeType = 'application/vnd.google-apps.folder'" in q:
            items = [x for x in items if x.get("mimeType") == "application/vnd.google-apps.folder"]
        elif "mimeType != 'application/vnd.google-apps.folder'" in q:
            items = [x for x in items if x.get("mimeType") != "application/vnd.google-apps.folder"]
        return Request({"files": items})

    def get(self, fileId, fields, **kwargs):
        return Request({"id": fileId, "parents": self.parents.get(fileId, [])})

    def create(self, body, fields, **kwargs):
        folder_id = f"created-{len(self.created) + 1}"
        self.created.append(body)
        self.children.setdefault(body["parents"][0], []).append({"id": folder_id, "name": body["name"], "mimeType": body["mimeType"]})
        self.children[folder_id] = []
        return Request({"id": folder_id})

    def update(self, fileId, addParents=None, removeParents=None, body=None, fields="", **kwargs):
        if self.deny_trash and (body or {}).get("trashed"):
            raise PermissionError("owned by another user")
        self.updated.append({"fileId": fileId, "addParents": addParents, "removeParents": removeParents, "body": body})
        if addParents:
            self.parents[fileId] = [addParents]
        return Request({"id": fileId, "parents": self.parents.get(fileId, []), "trashed": bool((body or {}).get("trashed"))})


def invoice(**values):
    data = dict(
        max_date="2026-06-24 10:00:00", mode="???", chat="chat", author="?????",
        file_name="invoice.pdf", file_type="pdf", operation_type="??????",
        payment_type="??????????? ??? ???", bank="", counterparty='??? "???????"',
        invoice_number="15", invoice_date="2026-06-01", object_name="??? ??????",
        project="????", budget_item="??????????", responsible="?????.?", purpose="??????",
        amount="1000", payment_status="\u041e\u043f\u043b\u0430\u0447\u0435\u043d",
        google_drive_link="https://drive.google.com/file/d/invoice-file/view",
        max_message_id="mid", max_file_id="fid", analysis_status="??",
    )
    data.update(values)
    return InvoiceArchiveRecord(**data)


def payment(**values):
    data = dict(name="pp.pdf", date="2026-06-24", operation_type="??????", payment_type="??????????? ??? ???",
                bank="?/?", counterparty='??? "???????"', invoice_number="15", object_name="??? ??????",
                project="????", budget_item="??????????", responsible="?????.?", purpose="??????",
                invoice_link="https://drive.google.com/file/d/invoice-file/view", amount="1000")
    data.update(values)
    return PaymentRecord(**data)


def test_payment_month_uses_payment_date_and_current_month_is_inside_object():
    drive = Drive()
    folder_id = resolve_payment_month_folder(
        drive, "root", "??? ??????", date(2026, 6, 24),
        {"drive_object_folders": {"??? ??????": "01_ ??? ??????"}}, today=date(2026, 6, 24),
    )
    assert folder_id == "created-1"
    assert drive.created[0]["name"] == "06 \u0438\u044e\u043d\u044c"
    assert drive.created[0]["parents"] == ["obj"]


def test_late_payment_archives_by_payment_month_not_invoice_month():
    drive = Drive()
    result = archive_paid_invoice(
        drive, "root", invoice(invoice_date="2026-06-01"), payment(date="2026-07-03"), True,
        {"drive_object_folders": {"??? ??????": "01_ ??? ??????"}}, today=date(2026, 7, 3),
    )
    assert result["status"] == "moved"
    assert drive.created[0]["name"] == "07 \u0438\u044e\u043b\u044c"
    assert drive.updated[-1]["fileId"] == "invoice-file"
    assert drive.updated[-1]["removeParents"] == "obj"


def test_paid_invoice_is_not_moved_before_final_row_is_confirmed():
    drive = Drive()
    result = archive_paid_invoice(
        drive, "root", invoice(), payment(), False,
        {"drive_object_folders": {"??? ??????": "01_ ??? ??????"}}, today=date(2026, 6, 24),
    )
    assert result["status"] == "skipped_final_not_confirmed"
    assert drive.updated == []


def test_legacy_review_files_move_to_double_underscore_and_folder_is_trashed():
    drive = Drive()
    result = migrate_legacy_review_folder(drive, "root")
    assert result == {"moved": 1, "legacy_folder_trashed": True}
    assert drive.parents["bad-file"] == ["unresolved"]
    assert drive.updated[-1]["body"] == {"trashed": True}



def test_legacy_review_permission_error_does_not_block_file_moves():
    drive = Drive()
    drive.deny_trash = True
    result = migrate_legacy_review_folder(drive, "root")
    assert result == {"moved": 1, "legacy_folder_trashed": False}
    assert drive.parents["bad-file"] == ["unresolved"]
