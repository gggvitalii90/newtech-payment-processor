from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    import truststore
except ImportError:
    truststore = None
else:
    truststore.inject_into_ssl()


MAX_API_BASE = "https://platform-api.max.ru"
DOWNLOAD_URL_KEYS = ("url", "download_url", "file_url", "href", "link")
FILENAME_KEYS = ("filename", "file_name", "name", "title", "caption")
FILE_ID_KEYS = ("file_id", "fileId", "id", "token")


@dataclass(frozen=True)
class FileCandidate:
    filename: str
    url: str
    file_id: str
    message_id: str
    timestamp: str

    @property
    def journal_key(self) -> str:
        raw = "|".join([self.message_id, self.filename, self.url, self.file_id])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


@dataclass
class DownloadSummary:
    downloaded: list[Path]
    skipped: list[str]
    no_url: list[str]
    sorted_payment_orders: list[Path] | None = None
    sorted_other_files: list[Path] | None = None


class MaxApiError(RuntimeError):
    pass


class MaxApiClient:
    def __init__(self, token: str, base_url: str = MAX_API_BASE) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    def get_messages(
        self,
        chat_id: str,
        count: int = 100,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"chat_id": chat_id, "count": count}
        if from_ts is not None:
            params["from"] = from_ts
        if to_ts is not None:
            params["to"] = to_ts
        payload = self._request_json("/messages", params)
        return normalize_messages_response(payload)

    def download_url(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = Request(url, headers=self._headers())
        try:
            with urlopen(request, timeout=60) as response:
                destination.write_bytes(response.read())
        except HTTPError as exc:
            if exc.code not in {401, 403}:
                raise MaxApiError(f"MAX download failed: HTTP {exc.code} {url}") from exc
            request = Request(url)
            try:
                with urlopen(request, timeout=60) as response:
                    destination.write_bytes(response.read())
            except (HTTPError, URLError) as second_exc:
                raise MaxApiError(f"MAX download failed: {second_exc}") from second_exc
        except URLError as exc:
            raise MaxApiError(f"MAX download failed: {exc}") from exc

    def _request_json(self, path: str, params: dict[str, Any]) -> Any:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(url, headers=self._headers())
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise MaxApiError(f"MAX API error HTTP {exc.code}: {details}") from exc
        except (URLError, json.JSONDecodeError) as exc:
            raise MaxApiError(f"MAX API error: {exc}") from exc

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.token, "Accept": "application/json"}


def normalize_messages_response(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("messages", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return [payload] if "body" in payload or "message_id" in payload else []


def extract_file_candidates(
    message: dict[str, Any],
    allowed_extensions: tuple[str, ...] = (".pdf",),
) -> list[FileCandidate]:
    body = message.get("body")
    if not isinstance(body, dict):
        body = message
    message_id = str(body.get("mid") or message.get("message_id") or message.get("id") or "")
    timestamp = str(message.get("timestamp") or message.get("created_at") or message.get("time") or "")
    return _extract_body_file_candidates(body, message_id, timestamp, allowed_extensions)


def extract_linked_file_candidates(
    message: dict[str, Any],
    allowed_extensions: tuple[str, ...] = (".pdf",),
) -> list[FileCandidate]:
    link = message.get("link")
    if not isinstance(link, dict):
        return []
    body = link.get("message")
    if not isinstance(body, dict):
        return []
    message_id = str(body.get("mid") or "")
    timestamp = str(message.get("timestamp") or message.get("created_at") or message.get("time") or "")
    return _extract_body_file_candidates(body, message_id, timestamp, allowed_extensions)



def extract_all_file_candidates(
    message: dict[str, Any],
    allowed_extensions: tuple[str, ...] = (".pdf",),
) -> list[FileCandidate]:
    candidates = [
        *extract_file_candidates(message, allowed_extensions),
        *extract_linked_file_candidates(message, allowed_extensions),
    ]
    unique: dict[str, FileCandidate] = {}
    for candidate in candidates:
        key = candidate.url or candidate.file_id or f"{candidate.message_id}:{candidate.filename}"
        current = unique.get(key)
        if current is None or _candidate_score(candidate) > _candidate_score(current):
            unique[key] = candidate
    return list(unique.values())

def _extract_body_file_candidates(
    body: dict[str, Any],
    message_id: str,
    timestamp: str,
    allowed_extensions: tuple[str, ...],
) -> list[FileCandidate]:
    attachments = body.get("attachments")
    if not isinstance(attachments, list):
        return []
    found: list[FileCandidate] = []

    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        for item in _walk_dicts(attachment):
            direct_url = _first_string(item, DOWNLOAD_URL_KEYS)
            url = direct_url or _first_nested_string(item, DOWNLOAD_URL_KEYS)
            filename = _detect_filename(item, url, message_id, len(found))
            file_id = _first_value_as_string(item, FILE_ID_KEYS) or _first_nested_value_as_string(item, FILE_ID_KEYS)
            if not url and not _looks_like_file_node(item):
                continue
            if direct_url and not _first_string(item, FILENAME_KEYS) and not Path(urlparse(url).path).suffix and not _looks_like_file_node(item):
                continue
            if allowed_extensions and not filename.lower().endswith(allowed_extensions):
                continue
            found.append(
                FileCandidate(
                    filename=sanitize_filename(filename),
                    url=url,
                    file_id=file_id,
                    message_id=message_id,
                    timestamp=timestamp,
                )
            )

    unique: dict[str, FileCandidate] = {}
    for candidate in found:
        key = candidate.url or candidate.file_id or candidate.filename
        current = unique.get(key)
        if current is None or _candidate_score(candidate) > _candidate_score(current):
            unique[key] = candidate
    return list(unique.values())


def download_chat_files(
    client: MaxApiClient,
    chat_id: str,
    target_dir: Path,
    count: int = 100,
    from_ts: int | None = None,
    to_ts: int | None = None,
    allowed_extensions: tuple[str, ...] = (".pdf",),
) -> DownloadSummary:
    target_dir.mkdir(parents=True, exist_ok=True)
    journal_path = target_dir / ".max_downloaded.json"
    journal = _load_journal(journal_path)
    downloaded: list[Path] = []
    skipped: list[str] = []
    no_url: list[str] = []

    for message in client.get_messages(chat_id, count=count, from_ts=from_ts, to_ts=to_ts):
        for candidate in extract_all_file_candidates(message, allowed_extensions):
            if not candidate.url:
                no_url.append(candidate.filename)
                continue
            if candidate.journal_key in journal:
                skipped.append(candidate.filename)
                continue
            destination = _unique_path(target_dir / candidate.filename)
            client.download_url(candidate.url, destination)
            journal[candidate.journal_key] = {
                "filename": candidate.filename,
                "saved_as": destination.name,
                "message_id": candidate.message_id,
                "file_id": candidate.file_id,
                "timestamp": candidate.timestamp,
                "url_hash": hashlib.sha1(candidate.url.encode("utf-8")).hexdigest(),
                "content_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
            downloaded.append(destination)

    _save_journal(journal_path, journal)
    return DownloadSummary(downloaded=downloaded, skipped=skipped, no_url=no_url)


def download_chat_files_for_date(
    client: MaxApiClient,
    chat_id: str,
    target_dir: Path,
    selected_date: date,
    count: int = 100,
    timezone_name: str = "Europe/Moscow",
    allowed_extensions: tuple[str, ...] = (),
) -> DownloadSummary:
    start_ts, end_ts = date_window_unix(selected_date, timezone_name)
    start_ts *= 1000
    end_ts *= 1000
    return download_chat_files_paged(
        client=client,
        chat_id=chat_id,
        target_dir=target_dir,
        count=count,
        from_ts=end_ts,
        to_ts=start_ts,
        allowed_extensions=allowed_extensions,
    )


def get_messages_for_date(
    client: MaxApiClient,
    chat_id: str,
    selected_date: date,
    count: int = 100,
    timezone_name: str = "Europe/Moscow",
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    start_ts, end_ts = date_window_unix(selected_date, timezone_name)
    return _get_messages_paged(client, chat_id, end_ts * 1000, start_ts * 1000, count, max_pages)


def get_messages_between_dates(
    client: MaxApiClient,
    chat_id: str,
    start_date: date,
    end_date: date,
    count: int = 100,
    timezone_name: str = "Europe/Moscow",
    max_pages: int = 1000,
) -> list[dict[str, Any]]:
    start_ts, _ = date_window_unix(start_date, timezone_name)
    _, end_ts = date_window_unix(end_date, timezone_name)
    return _get_messages_paged(client, chat_id, end_ts * 1000, start_ts * 1000, count, max_pages)


def _get_messages_paged(
    client: MaxApiClient,
    chat_id: str,
    page_from: int,
    to_ts: int,
    count: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    page_size = max(1, min(count, 100))
    seen_message_ids: set[str] = set()
    result: list[dict[str, Any]] = []

    for _page in range(max_pages):
        messages = client.get_messages(chat_id, count=page_size, from_ts=page_from, to_ts=to_ts)
        if not messages:
            break
        new_messages = [message for message in messages if _message_identity(message) not in seen_message_ids]
        for message in new_messages:
            seen_message_ids.add(_message_identity(message))
            result.append(message)

        oldest_ts = _oldest_message_timestamp(messages)
        if len(messages) < page_size or oldest_ts is None:
            break
        if oldest_ts <= to_ts:
            break
        next_from = oldest_ts - 1
        if next_from >= page_from:
            break
        page_from = next_from

    return result


def download_chat_files_paged(
    client: MaxApiClient,
    chat_id: str,
    target_dir: Path,
    count: int = 100,
    from_ts: int | None = None,
    to_ts: int | None = None,
    allowed_extensions: tuple[str, ...] = (),
    max_pages: int = 50,
) -> DownloadSummary:
    target_dir.mkdir(parents=True, exist_ok=True)
    journal_path = target_dir / ".max_downloaded.json"
    journal = _load_journal(journal_path)
    downloaded: list[Path] = []
    skipped: list[str] = []
    no_url: list[str] = []
    page_from = from_ts
    page_size = max(1, min(count, 100))
    seen_message_ids: set[str] = set()

    for _page in range(max_pages):
        messages = client.get_messages(chat_id, count=page_size, from_ts=page_from, to_ts=to_ts)
        if not messages:
            break
        new_messages = [message for message in messages if _message_identity(message) not in seen_message_ids]
        for message in new_messages:
            seen_message_ids.add(_message_identity(message))
            for candidate in extract_all_file_candidates(message, allowed_extensions):
                if not candidate.url:
                    no_url.append(candidate.filename)
                    continue
                if candidate.journal_key in journal:
                    skipped.append(candidate.filename)
                    continue
                destination = _unique_path(target_dir / candidate.filename)
                client.download_url(candidate.url, destination)
                journal[candidate.journal_key] = {
                    "filename": candidate.filename,
                    "saved_as": destination.name,
                    "message_id": candidate.message_id,
                    "file_id": candidate.file_id,
                    "timestamp": candidate.timestamp,
                    "url_hash": hashlib.sha1(candidate.url.encode("utf-8")).hexdigest(),
                    "content_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                }
                downloaded.append(destination)

        oldest_ts = _oldest_message_timestamp(messages)
        if len(messages) < page_size or oldest_ts is None:
            break
        if to_ts is not None and oldest_ts <= to_ts:
            break
        next_from = oldest_ts - 1
        if page_from is not None and next_from >= page_from:
            break
        page_from = next_from

    _save_journal(journal_path, journal)
    return DownloadSummary(downloaded=downloaded, skipped=skipped, no_url=no_url)


def sort_downloaded_files(
    files: list[Path],
    payment_dir: Path,
    other_dir: Path,
    is_payment_order_pdf,
) -> tuple[list[Path], list[Path]]:
    payment_dir.mkdir(parents=True, exist_ok=True)
    other_dir.mkdir(parents=True, exist_ok=True)
    payment_orders: list[Path] = []
    other_files: list[Path] = []

    for path in files:
        if not path.exists():
            continue
        is_payment_order = is_payment_order_pdf(path)
        if not path.exists():
            continue
        destination_dir = payment_dir if is_payment_order else other_dir
        existing = _find_existing_duplicate(path, destination_dir)
        if existing:
            if path.resolve() != existing.resolve():
                path.unlink(missing_ok=True)
            moved = existing
        else:
            destination = _unique_path(destination_dir / path.name)
            if path.resolve() == destination.resolve():
                moved = path
            else:
                moved = Path(shutil.move(str(path), str(destination)))
        if destination_dir == payment_dir:
            payment_orders.append(moved)
        else:
            other_files.append(moved)
    _remove_exact_duplicate_suffixes(payment_dir)
    _remove_exact_duplicate_suffixes(other_dir)
    return _unique_paths([path for path in payment_orders if path.exists()]), _unique_paths([path for path in other_files if path.exists()])


def _find_existing_duplicate(path: Path, destination_dir: Path) -> Path | None:
    if not destination_dir.exists():
        return None
    candidates = [
        candidate
        for candidate in destination_dir.iterdir()
        if candidate.is_file() and candidate.resolve() != path.resolve() and _same_duplicate_family(path, candidate)
    ]
    try:
        path_hash = _file_hash(path)
    except FileNotFoundError:
        return None
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and _file_hash(candidate) == path_hash:
            return candidate
    return None


def _same_duplicate_family(source: Path, candidate: Path) -> bool:
    if candidate.name == source.name:
        return True
    if candidate.suffix.lower() != source.suffix.lower():
        return False
    candidate_base = _suffix_duplicate_base(candidate)
    if candidate_base and candidate_base.name == source.name:
        return True
    source_base = _suffix_duplicate_base(source)
    return bool(source_base and source_base.name == candidate.name)


def _remove_exact_duplicate_suffixes(folder: Path) -> None:
    if not folder.exists():
        return
    for path in list(folder.iterdir()):
        if not path.is_file():
            continue
        base = _suffix_duplicate_base(path)
        if not base or not base.exists():
            continue
        if _file_hash(base) == _file_hash(path):
            path.unlink(missing_ok=True)


def _suffix_duplicate_base(path: Path) -> Path | None:
    match = re.match(r"^(?P<stem>.+)_(?P<index>\d+)$", path.stem)
    if not match:
        return None
    return path.with_name(f"{match.group('stem')}{path.suffix}")


def _unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


def _file_hash(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def date_window_unix(selected_date: date, timezone_name: str = "Europe/Moscow") -> tuple[int, int]:
    timezone = ZoneInfo(timezone_name)
    start = datetime.combine(selected_date, time.min, timezone)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return int(start.timestamp()), int(end.timestamp())


def build_client_from_env(env: dict[str, str] | None = None, mode: str = "ПСК") -> tuple[MaxApiClient, str, int]:
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    token = merged_env.get("MAX_BOT_TOKEN", "").strip()
    chat_id = chat_id_from_env(merged_env, mode)
    count = int(merged_env.get("MAX_MESSAGE_COUNT", "100") or "100")
    if not token:
        raise MaxApiError("Не найден MAX_BOT_TOKEN")
    if not chat_id:
        raise MaxApiError(_missing_chat_message(mode))
    return MaxApiClient(token), chat_id, max(1, min(count, 100))


def normalize_mode_key(mode: str) -> str:
    mode_key = (mode or "").strip().upper()
    if mode_key in {"IS", "ИС"}:
        return "ИС"
    if mode_key in {"PSK", "ПСК"}:
        return "ПСК"
    return mode_key


def chat_id_from_env(env: dict[str, str], mode: str = "ПСК") -> str:
    mode_key = normalize_mode_key(mode)
    if mode_key == "ИС":
        return (
            env.get("MAX_IS_CHAT_ID", "").strip()
            or env.get("MAX_INVESTSTROY_CHAT_ID", "").strip()
            or env.get("MAX_CHAT_ID", "").strip()
        )
    return env.get("MAX_PSK_BEZNAL_CHAT_ID", "").strip() or env.get("MAX_CHAT_ID", "").strip()



def cash_chat_id_from_env(env: dict[str, str], mode: str = "ПСК") -> str:
    mode_key = normalize_mode_key(mode)
    if mode_key == "\u0418\u0421":
        return (
            env.get("MAX_IS_CASH_CHAT_ID", "").strip()
            or env.get("MAX_IS_CHAT_ID", "").strip()
            or env.get("MAX_INVESTSTROY_CHAT_ID", "").strip()
        )
    return env.get("MAX_CASH_CHAT_ID", "").strip() or env.get("MAX_PSK_NAL_CHAT_ID", "").strip()

def _missing_chat_message(mode: str) -> str:
    mode_key = normalize_mode_key(mode)
    if mode_key == "ИС":
        return "Не найден MAX_IS_CHAT_ID или MAX_INVESTSTROY_CHAT_ID"
    return "Не найден MAX_CHAT_ID"


def _message_identity(message: dict[str, Any]) -> str:
    return str(message.get("message_id") or message.get("id") or json.dumps(message, sort_keys=True, ensure_ascii=False))


def _oldest_message_timestamp(messages: list[dict[str, Any]]) -> int | None:
    timestamps = [_message_timestamp(message) for message in messages]
    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    return min(timestamps) if timestamps else None


def _message_timestamp(message: dict[str, Any]) -> int | None:
    value = message.get("timestamp") or message.get("created_at") or message.get("time")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int):
        return None
    return value


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        result.append(value)
        for child in value.values():
            result.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_walk_dicts(child))
    return result


def _first_string(item: dict[str, Any], keys) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_value_as_string(item: dict[str, Any], keys) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return ""


def _first_nested_string(item: dict[str, Any], keys) -> str:
    for child in _walk_dicts(item):
        if child is item:
            continue
        value = _first_string(child, keys)
        if value:
            return value
    return ""


def _first_nested_value_as_string(item: dict[str, Any], keys) -> str:
    for child in _walk_dicts(item):
        if child is item:
            continue
        value = _first_value_as_string(child, keys)
        if value:
            return value
    return ""


def _detect_filename(item: dict[str, Any], url: str, message_id: str, index: int) -> str:
    filename = _first_string(item, FILENAME_KEYS)
    if filename:
        return filename
    if url:
        parsed_name = Path(urlparse(url).path).name
        if parsed_name:
            return parsed_name
    suffix = f"_{index + 1}" if index else ""
    return f"max_message_{message_id or 'unknown'}{suffix}.pdf"


def _candidate_score(candidate: FileCandidate) -> int:
    score = 0
    if candidate.filename.lower().endswith(".pdf"):
        score += 3
    if Path(candidate.filename).suffix:
        score += 2
    if candidate.url and candidate.filename != Path(urlparse(candidate.url).path).name:
        score += 4
    if candidate.file_id:
        score += 1
    return score


def _looks_like_file_node(item: dict[str, Any]) -> bool:
    type_value = str(item.get("type") or item.get("media_type") or item.get("attachment_type") or "").lower()
    has_name = bool(_first_string(item, FILENAME_KEYS))
    has_id = bool(_first_string(item, FILE_ID_KEYS))
    if any(word in type_value for word in ("file", "document", "pdf")) and (has_name or has_id):
        return True
    return bool(has_name and has_id)


def sanitize_filename(filename: str) -> str:
    cleaned = "".join("_" if char in '<>:"/\\|?*' else char for char in filename).strip()
    cleaned = cleaned.rstrip(". ")
    return cleaned or "max_file.pdf"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise MaxApiError(f"Не удалось подобрать имя файла для {path.name}")


def _load_journal(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_journal(path: Path, journal: dict[str, Any]) -> None:
    path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
