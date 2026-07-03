from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .env import load_env


DEFAULT_FINTABLO_BASE_URL = "https://api.fintablo.ru"


class FinTabloError(RuntimeError):
    pass


@dataclass(frozen=True)
class FinTabloSettings:
    token: str
    base_url: str = DEFAULT_FINTABLO_BASE_URL


@dataclass(frozen=True)
class FinTabloResponse:
    status: int
    items: list[dict[str, Any]]
    request_id: str = ""


Transport = Callable[[Request, int], tuple[int, dict[str, str], bytes]]


def load_fintablo_settings(env: dict[str, str] | None = None) -> FinTabloSettings:
    if env is None:
        env = load_env()
    token = (env.get("FINTABLO_API_TOKEN") or "").strip()
    if not token:
        raise FinTabloError("?? ?????? FINTABLO_API_TOKEN ? .env")
    base_url = (env.get("FINTABLO_BASE_URL") or DEFAULT_FINTABLO_BASE_URL).strip().rstrip("/")
    return FinTabloSettings(token=token, base_url=base_url)


def default_transport(request: Request, timeout: int) -> tuple[int, dict[str, str], bytes]:
    try:
        with urlopen(request, timeout=timeout) as response:
            headers = {key: value for key, value in response.headers.items()}
            return int(response.status), headers, response.read()
    except HTTPError as exc:
        body = exc.read()
        headers = {key: value for key, value in exc.headers.items()}
        return int(exc.code), headers, body
    except URLError as exc:
        raise FinTabloError(f"FinTablo API ??????????: {exc}") from exc


class FinTabloClient:
    def __init__(
        self,
        settings: FinTabloSettings,
        *,
        timeout: int = 30,
        transport: Transport = default_transport,
    ) -> None:
        self.settings = settings
        self.timeout = timeout
        self.transport = transport

    def list_categories(self, **params: Any) -> list[dict[str, Any]]:
        return self.get_all("/v1/category", params=params)

    def list_moneybags(self, **params: Any) -> list[dict[str, Any]]:
        return self.get_all("/v1/moneybag", params=params)

    def list_partners(self, **params: Any) -> list[dict[str, Any]]:
        return self.get_all("/v1/partner", params=params)

    def list_directions(self, **params: Any) -> list[dict[str, Any]]:
        return self.get_all("/v1/direction", params=params)

    def list_deals(self, **params: Any) -> list[dict[str, Any]]:
        return self.get_all("/v1/deal", params=params, page_size=None)

    def list_employees(self, **params: Any) -> list[dict[str, Any]]:
        return self.get_all("/v1/employees", params=params)

    def list_transactions(self, *, date_from: str, date_to: str, **params: Any) -> list[dict[str, Any]]:
        query = {"dateFrom": date_from, "dateTo": date_to, **params}
        return self.get_all("/v1/transaction", params=query)

    def create_category(self, payload: dict[str, Any]) -> FinTabloResponse:
        return self.request_json("POST", "/v1/category", payload=payload)

    def update_category(self, category_id: int | str, payload: dict[str, Any]) -> FinTabloResponse:
        return self.request_json("PUT", f"/v1/category/{category_id}", payload=payload)

    def delete_category(self, category_id: int | str) -> FinTabloResponse:
        return self.request_json("DELETE", f"/v1/category/{category_id}")

    def create_deal(self, payload: dict[str, Any]) -> FinTabloResponse:
        return self.request_json("POST", "/v1/deal", payload=payload)

    def add_deal_stage(self, deal_id: int | str, name: str) -> FinTabloResponse:
        return self.request_json("POST", f"/v1/deal/{deal_id}/add-stage", payload={"name": name})

    def get_all(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_size: int | None = 1000,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        params = {key: value for key, value in (params or {}).items() if value not in (None, "")}
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            request_params = dict(params)
            if page_size is not None:
                request_params.setdefault("pageSize", page_size)
            request_params.setdefault("page", page)
            response = self.get(path, params=request_params)
            items.extend(response.items)
            if page_size is None or len(response.items) < page_size:
                break
            page += 1
            if page > max_pages:
                raise FinTabloError(f"FinTablo pagination exceeded {max_pages} pages for {path}")
        return items

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> FinTabloResponse:
        query = urlencode({key: value for key, value in (params or {}).items() if value not in (None, "")})
        url = f"{self.settings.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.settings.token}",
                "Accept": "application/json",
            },
            method="GET",
        )
        status, headers, body = self.transport(request, self.timeout)
        request_id = headers.get("X-Request-Id") or headers.get("x-request-id") or ""
        payload = _decode_payload(body)
        if status < 200 or status >= 300:
            message = payload.get("statusText") or payload.get("message") or body.decode("utf-8", errors="replace")
            raise FinTabloError(f"FinTablo API HTTP {status}: {message}; request_id={request_id}")
        if int(payload.get("status") or status) != 200:
            raise FinTabloError(f"FinTablo API status {payload.get('status')}: {payload.get('statusText', '')}; request_id={request_id}")
        raw_items = payload.get("items") or []
        if not isinstance(raw_items, list):
            raise FinTabloError(f"FinTablo API returned non-list items for {path}; request_id={request_id}")
        return FinTabloResponse(status=int(payload.get("status") or status), items=raw_items, request_id=request_id)

    def request_json(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> FinTabloResponse:
        data = None
        headers = {
            "Authorization": f"Bearer {self.settings.token}",
            "Accept": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(
            f"{self.settings.base_url}{path}",
            data=data,
            headers=headers,
            method=method.upper(),
        )
        status, response_headers, body = self.transport(request, self.timeout)
        request_id = response_headers.get("X-Request-Id") or response_headers.get("x-request-id") or ""
        payload_data = _decode_payload(body)
        if status < 200 or status >= 300:
            message = payload_data.get("statusText") or payload_data.get("message") or body.decode("utf-8", errors="replace")
            raise FinTabloError(f"FinTablo API HTTP {status}: {message}; request_id={request_id}")
        if int(payload_data.get("status") or status) != 200:
            raise FinTabloError(f"FinTablo API status {payload_data.get('status')}: {payload_data.get('statusText', '')}; request_id={request_id}")
        raw_items = payload_data.get("items") or []
        if raw_items and not isinstance(raw_items, list):
            raise FinTabloError(f"FinTablo API returned non-list items for {path}; request_id={request_id}")
        return FinTabloResponse(status=int(payload_data.get("status") or status), items=raw_items, request_id=request_id)


def _decode_payload(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FinTabloError(f"FinTablo API returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FinTabloError("FinTablo API returned non-object JSON")
    return payload
