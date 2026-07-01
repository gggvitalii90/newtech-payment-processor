from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import date
from pathlib import Path

from payment_processor.max_api import (
    FileCandidate,
    date_window_unix,
    download_chat_files,
    download_chat_files_for_date,
    extract_file_candidates,
    extract_linked_file_candidates,
    get_messages_between_dates,
    get_messages_for_date,
    normalize_messages_response,
    sanitize_filename,
    sort_downloaded_files,
    build_client_from_env,
    cash_chat_id_from_env,
)
from payment_processor.payment_classifier import is_payment_order_text






def test_cash_chat_id_from_env_uses_psk_cash_chat_for_psk_mode() -> None:
    chat_id = cash_chat_id_from_env(
        {
            "MAX_CASH_CHAT_ID": "psk-cash",
            "MAX_INVESTSTROY_CHAT_ID": "is-chat",
        },
        mode="\u041f\u0421\u041a",
    )

    assert chat_id == "psk-cash"


def test_cash_chat_id_from_env_uses_investstroy_chat_for_is_mode() -> None:
    chat_id = cash_chat_id_from_env(
        {
            "MAX_CASH_CHAT_ID": "psk-cash",
            "MAX_INVESTSTROY_CHAT_ID": "is-chat",
        },
        mode="\u0418\u0421",
    )

    assert chat_id == "is-chat"

def test_build_client_from_env_uses_investstroy_chat_for_is_mode() -> None:
    _client, chat_id, count = build_client_from_env(
        {
            "MAX_BOT_TOKEN": "token",
            "MAX_CHAT_ID": "psk-chat",
            "MAX_INVESTSTROY_CHAT_ID": "is-chat",
            "MAX_MESSAGE_COUNT": "150",
        },
        mode="\u0418\u0421",
    )

    assert chat_id == "is-chat"
    assert count == 100


def test_build_client_from_env_uses_investstroy_chat_for_ascii_is_mode() -> None:
    _client, chat_id, count = build_client_from_env(
        {
            "MAX_BOT_TOKEN": "token",
            "MAX_CHAT_ID": "psk-chat",
            "MAX_INVESTSTROY_CHAT_ID": "is-chat",
            "MAX_MESSAGE_COUNT": "150",
        },
        mode="IS",
    )

    assert chat_id == "is-chat"
    assert count == 100


def test_build_client_from_env_uses_main_chat_for_psk_mode() -> None:
    _client, chat_id, _count = build_client_from_env(
        {
            "MAX_BOT_TOKEN": "token",
            "MAX_CHAT_ID": "psk-chat",
            "MAX_INVESTSTROY_CHAT_ID": "is-chat",
        },
        mode="\u041f\u0421\u041a",
    )

    assert chat_id == "psk-chat"

def test_extract_file_candidates_from_nested_message() -> None:
    message = {
        "message_id": "m1",
        "body": {
            "attachments": [
                {
                    "type": "file",
                    "payload": {
                        "file_name": "Платежное поручение №1.pdf",
                        "download_url": "https://files.example/payments/1.pdf",
                    },
                },
                {
                    "type": "image",
                    "payload": {
                        "file_name": "photo.jpg",
                        "download_url": "https://files.example/photo.jpg",
                    },
                },
            ]
        },
    }

    candidates = extract_file_candidates(message)

    assert candidates == [
        FileCandidate(
            filename="Платежное поручение №1.pdf",
            url="https://files.example/payments/1.pdf",
            file_id="",
            message_id="m1",
            timestamp="",
        )
    ]


def test_extract_file_candidates_from_real_max_file_shape() -> None:
    message = {
        "body": {
            "mid": "mid.1",
            "text": "",
        },
        "link": {
            "type": "forward",
            "message": {
                "mid": "mid.2",
                "attachments": [
                    {
                        "payload": {
                            "url": "https://fd.oneme.ru/getfile?sig=abc&id=3824247568",
                            "token": "file-token",
                            "fileId": 3824247568,
                        },
                        "filename": "1557357629.193373309000243158.1.2.pdf",
                        "size": 251065,
                        "type": "file",
                    }
                ],
            },
        },
        "timestamp": 1781025181546,
    }

    candidates = extract_linked_file_candidates(message, allowed_extensions=())

    assert len(candidates) == 1
    assert candidates[0].filename == "1557357629.193373309000243158.1.2.pdf"
    assert candidates[0].url.startswith("https://fd.oneme.ru/getfile")
    assert candidates[0].file_id == "3824247568"


def test_extract_file_candidates_does_not_take_linked_attachment() -> None:
    message = {
        "timestamp": 1781025181546,
        "body": {"mid": "outer", "text": "Объект: ПСК", "attachments": []},
        "link": {
            "type": "reply",
            "message": {
                "mid": "inner",
                "text": "",
                "attachments": [
                    {
                        "type": "file",
                        "filename": "invoice.pdf",
                        "payload": {"url": "https://files.example/invoice.pdf", "token": "file-token"},
                    }
                ],
            },
        },
    }

    assert extract_file_candidates(message, allowed_extensions=()) == []
    linked = extract_linked_file_candidates(message, allowed_extensions=())
    assert len(linked) == 1
    assert linked[0].message_id == "inner"
    assert linked[0].filename == "invoice.pdf"


def test_extract_file_candidates_reports_file_without_url() -> None:
    message = {
        "id": "m2",
        "attachments": [{"type": "document", "filename": "invoice.pdf", "file_id": "abc"}],
    }

    candidates = extract_file_candidates(message)

    assert len(candidates) == 1
    assert candidates[0].filename == "invoice.pdf"
    assert candidates[0].url == ""
    assert candidates[0].file_id == "abc"


def test_normalize_messages_response_supports_common_shapes() -> None:
    assert normalize_messages_response({"messages": [{"id": 1}]}) == [{"id": 1}]
    assert normalize_messages_response({"items": [{"id": 2}]}) == [{"id": 2}]
    assert normalize_messages_response([{"id": 3}, "bad"]) == [{"id": 3}]


def test_sanitize_filename_removes_windows_forbidden_chars() -> None:
    assert sanitize_filename('a<b>:"/\\|?*.pdf') == "a_b________.pdf"


class FakeMaxClient:
    def __init__(self) -> None:
        self.downloads: list[tuple[str, Path]] = []
        self.messages = [
            {
                "message_id": "m1",
                "attachments": [
                    {
                        "type": "file",
                        "filename": "payment.pdf",
                        "url": "https://files.example/payment.pdf",
                    }
                ],
            }
        ]

    def get_messages(self, chat_id, count=100, from_ts=None, to_ts=None):
        return self.messages

    def download_url(self, url: str, destination: Path) -> None:
        self.downloads.append((url, destination))
        destination.write_bytes(b"pdf")


def test_download_chat_files_uses_journal_to_skip_duplicates(tmp_path: Path) -> None:
    client = FakeMaxClient()

    first = download_chat_files(client, "chat", tmp_path)
    second = download_chat_files(client, "chat", tmp_path)

    assert [path.name for path in first.downloaded] == ["payment.pdf"]
    assert second.downloaded == []
    assert second.skipped == ["payment.pdf"]
    assert len(client.downloads) == 1
    journal = json.loads((tmp_path / ".max_downloaded.json").read_text(encoding="utf-8"))
    entry = next(iter(journal.values()))
    assert entry["file_id"] == ""
    assert entry["content_sha256"] == hashlib.sha256(b"pdf").hexdigest()


class FakePagedMaxClient:
    def __init__(self) -> None:
        self.calls = []
        self.pages = [
            [
                {
                    "message_id": "m3",
                    "timestamp": 1_800_000,
                    "attachments": [{"type": "file", "filename": "three.xlsx", "url": "https://files.example/3.xlsx"}],
                },
                {
                    "message_id": "m2",
                    "timestamp": 1_700_000,
                    "attachments": [{"type": "file", "filename": "two.docx", "url": "https://files.example/2.docx"}],
                },
            ],
            [
                {
                    "message_id": "m1",
                    "timestamp": 1_600_000,
                    "attachments": [{"type": "file", "filename": "one.pdf", "url": "https://files.example/1.pdf"}],
                }
            ],
        ]

    def get_messages(self, chat_id, count=100, from_ts=None, to_ts=None):
        self.calls.append({"chat_id": chat_id, "count": count, "from": from_ts, "to": to_ts})
        return self.pages.pop(0) if self.pages else []

    def download_url(self, url: str, destination: Path) -> None:
        destination.write_bytes(url.encode("utf-8"))


def test_download_chat_files_for_date_uses_moscow_window_and_paginates(tmp_path: Path) -> None:
    client = FakePagedMaxClient()

    summary = download_chat_files_for_date(client, "chat", tmp_path, date(1970, 1, 1), count=2)

    assert [path.name for path in summary.downloaded] == ["three.xlsx", "two.docx", "one.pdf"]
    assert client.calls[0]["from"] == (20 * 60 * 60 + 59 * 60 + 59) * 1000
    assert client.calls[0]["to"] == -3 * 60 * 60 * 1000
    assert client.calls[1]["from"] == 1_699_999


def test_get_messages_for_date_uses_same_pagination_without_downloading() -> None:
    client = FakePagedMaxClient()

    messages = get_messages_for_date(client, "chat", date(1970, 1, 1), count=2)

    assert [message["message_id"] for message in messages] == ["m3", "m2", "m1"]
    assert client.calls[0]["from"] == (20 * 60 * 60 + 59 * 60 + 59) * 1000
    assert client.calls[0]["to"] == -3 * 60 * 60 * 1000


def test_get_messages_between_dates_uses_one_paginated_window() -> None:
    client = FakePagedMaxClient()

    messages = get_messages_between_dates(client, "chat", date(1970, 1, 1), date(1970, 1, 2), count=2)

    assert [message["message_id"] for message in messages] == ["m3", "m2", "m1"]
    assert client.calls[0]["from"] == 161999000
    assert client.calls[0]["to"] == -10800000


def test_date_window_unix_uses_moscow_timezone() -> None:
    start, end = date_window_unix(date(2026, 6, 9))

    assert end - start == 86_399


def test_sort_downloaded_files_separates_payment_orders(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    payment_dir = tmp_path / "payments"
    other_dir = tmp_path / "other"
    inbox.mkdir()
    payment = inbox / "payment.pdf"
    other = inbox / "scan.jpg"
    payment.write_bytes(b"pdf")
    other.write_bytes(b"jpg")

    payments, others = sort_downloaded_files(
        [payment, other],
        payment_dir,
        other_dir,
        lambda path: path.suffix.lower() == ".pdf",
    )

    assert [path.name for path in payments] == ["payment.pdf"]
    assert [path.name for path in others] == ["scan.jpg"]
    assert (payment_dir / "payment.pdf").exists()
    assert (other_dir / "scan.jpg").exists()


def test_sort_downloaded_files_reuses_existing_identical_file(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    payment_dir = tmp_path / "payments"
    other_dir = tmp_path / "other"
    inbox.mkdir()
    payment_dir.mkdir()
    existing = payment_dir / "payment.pdf"
    existing.write_bytes(b"same pdf")
    downloaded = inbox / "payment.pdf"
    downloaded.write_bytes(b"same pdf")

    payments, others = sort_downloaded_files(
        [downloaded],
        payment_dir,
        other_dir,
        lambda path: path.suffix.lower() == ".pdf",
    )

    assert payments == [existing]
    assert others == []
    assert not downloaded.exists()
    assert not (payment_dir / "payment_2.pdf").exists()


def test_sort_downloaded_files_removes_existing_exact_suffix_duplicate(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    payment_dir = tmp_path / "payments"
    other_dir = tmp_path / "other"
    inbox.mkdir()
    payment_dir.mkdir()
    base = payment_dir / "payment.pdf"
    duplicate = payment_dir / "payment_2.pdf"
    base.write_bytes(b"same pdf")
    duplicate.write_bytes(b"same pdf")

    payments, _others = sort_downloaded_files(
        [],
        payment_dir,
        other_dir,
        lambda path: path.suffix.lower() == ".pdf",
    )

    assert payments == []
    assert base.exists()
    assert not duplicate.exists()


def test_sort_downloaded_files_does_not_return_removed_suffix_duplicate(tmp_path: Path) -> None:
    payment_dir = tmp_path / "payments"
    other_dir = tmp_path / "other"
    payment_dir.mkdir()
    base = payment_dir / "payment.pdf"
    duplicate = payment_dir / "payment_2.pdf"
    base.write_bytes(b"same pdf")
    duplicate.write_bytes(b"same pdf")

    payments, _others = sort_downloaded_files(
        [duplicate],
        payment_dir,
        other_dir,
        lambda path: path.suffix.lower() == ".pdf",
    )

    assert payments == [base]
    assert not duplicate.exists()


def test_is_payment_order_text_requires_payment_markers() -> None:
    payment_text = """
    ПЛАТЕЖНОЕ ПОРУЧЕНИЕ № 1
    Плательщик ООО Ромашка
    Банк плательщика
    БИК 044030653
    Получатель ООО Василек
    Банк получателя
    Сч. № 40702810000000000000
    """
    invoice_text = "Счет на оплату №1 Плательщик Получатель БИК Сч. №"

    assert is_payment_order_text(payment_text)
    assert not is_payment_order_text(invoice_text)


def test_is_payment_order_text_allows_contract_word_in_purpose() -> None:
    payment_text = """
    ПЛАТЕЖНОЕ ПОРУЧЕНИЕ № 528
    Плательщик ООО Ромашка
    Банк плательщика
    БИК 044030653
    Получатель ООО Василек
    Банк получателя
    Сч. № 40702810000000000000
    Возврат на основании соглашения о расторжении договора подряда.
    """

    assert is_payment_order_text(payment_text)


def test_download_chat_files_downloads_linked_reply_files() -> None:
    tmp_path = Path(f".test_tmp_linked_download_{uuid.uuid4().hex}")
    if tmp_path.exists():
        shutil.rmtree(tmp_path, ignore_errors=True)
    try:
        client = FakeMaxClient()
        client.messages = [
            {
                "timestamp": 1781025181546,
                "body": {"mid": "outer", "text": "", "attachments": []},
                "link": {
                    "type": "reply",
                    "message": {
                        "mid": "inner",
                        "attachments": [
                            {
                                "type": "file",
                                "filename": "1557357629.193373309000243294.1.2.pdf",
                                "payload": {"url": "https://files.example/linked.pdf", "fileId": 123},
                            }
                        ],
                    },
                },
            }
        ]

        summary = download_chat_files(client, "chat", tmp_path)

        assert [path.name for path in summary.downloaded] == ["1557357629.193373309000243294.1.2.pdf"]
        assert client.downloads[0][0] == "https://files.example/linked.pdf"
    finally:
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)

