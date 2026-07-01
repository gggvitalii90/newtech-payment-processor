import hashlib
from pathlib import Path

from payment_processor.google_api import upload_file_to_folder


class FakeDriveFiles:
    def __init__(self, checksum: str):
        self.created = []
        self.checksum = checksum

    def list(self, q, fields, supportsAllDrives, includeItemsFromAllDrives, pageSize):
        return FakeExecute(
            {
                "files": [
                    {
                        "id": "existing",
                        "name": "invoice.pdf",
                        "webViewLink": "https://drive.example/existing",
                        "md5Checksum": self.checksum,
                    }
                ]
            }
        )

    def create(self, body, media_body, fields, supportsAllDrives):
        self.created.append(body)
        return FakeExecute({"id": "new", "webViewLink": "https://drive.example/new"})


class FakeDrive:
    def __init__(self, checksum: str):
        self.files_api = FakeDriveFiles(checksum)

    def files(self):
        return self.files_api


class FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


def test_upload_file_to_folder_reuses_existing_drive_file(tmp_path: Path) -> None:
    file_path = tmp_path / "invoice.pdf"
    file_path.write_bytes(b"pdf")
    drive = FakeDrive(hashlib.md5(b"pdf").hexdigest())

    link = upload_file_to_folder(drive, file_path, "folder")

    assert link == "https://drive.example/existing"
    assert drive.files_api.created == []


def test_upload_file_to_folder_uploads_different_content_with_same_name(tmp_path: Path) -> None:
    file_path = tmp_path / "invoice.pdf"
    file_path.write_bytes(b"new pdf")
    drive = FakeDrive(hashlib.md5(b"old pdf").hexdigest())

    link = upload_file_to_folder(drive, file_path, "folder")

    assert link == "https://drive.example/new"
    assert drive.files_api.created == [{"name": "invoice.pdf", "parents": ["folder"]}]
