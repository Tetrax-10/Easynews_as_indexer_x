"""
Easynews API-like client (unofficial) to perform searches and download NZB files.

This client mimics the webapp behavior by calling:
- GET /2.0/search/solr-search for search results (JSON)
- POST /2.0/api/dl-nzb to create/download NZB for selected items

Authentication is cookie-based via username/password POST to the login endpoint.
You'll need a valid Easynews account. Use responsibly and per Easynews TOS.
"""

from __future__ import annotations

import base64
import logging
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TypeVar

import requests
from requests.exceptions import RequestException


EASYNEWS_BASE = "https://members.easynews.com"

_LOGIN_TIMEOUT = 15
_SEARCH_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 60

logger = logging.getLogger(__name__)

# Type variable for retry return type
T = TypeVar("T")


class EasynewsError(Exception):
    pass


@dataclass
class SearchItem:
    id: Optional[str]
    hash: str
    filename: str
    ext: str
    sig: Optional[str]
    type: str
    raw: Dict[str, Any]

    @property
    def value_token(self) -> str:
        """
        Build the value string Easynews expects for checkbox selections:
        format: "{hash}|{b64(filename)}:{b64(ext)}"
        As seen in members.js createNZB -> it reads from input[checkbox].value
        """
        fn_b64 = base64.b64encode(self.filename.encode()).decode().replace("=", "")
        ext_b64 = base64.b64encode(self.ext.encode()).decode().replace("=", "")
        return f"{self.hash}|{fn_b64}:{ext_b64}"


def _retry(
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple = (RequestException,),
    on_retryable_response: Optional[Callable[[requests.Response], bool]] = None,
) -> T:
    """
    Call *fn* with exponential backoff + random jitter on transient failures.

    - Retries on network errors (RequestException subclasses) and optionally
      on specific HTTP status codes (5xx) via *on_retryable_response*.
    - Delay = min(base_delay * 2^attempt + random(0..base_delay), max_delay)
    """
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            result = fn()
            if attempt > 0 and isinstance(result, requests.Response):
                if on_retryable_response and on_retryable_response(result):
                    logger.info(
                        "Retryable response %s on attempt %d, backing off",
                        result.status_code,
                        attempt,
                    )
                else:
                    return result
            else:
                return result
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, base_delay), max_delay)
                logger.warning(
                    "Transient error on attempt %d/%d (%s), retrying in %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    exc,
                    delay,
                )
                time.sleep(delay)
            else:
                logger.error("Exhausted %d retries for %s", max_retries + 1, fn.__name__ if hasattr(fn, "__name__") else fn)
                raise
        except EasynewsError:
            # Non-transient domain errors — fail fast
            raise

    # Should not reach here, but safety net
    if last_exc:
        raise last_exc
    return fn()  # type: ignore[unreachable]


class EasynewsClient:
    def __init__(
        self, username: str, password: str, session: Optional[requests.Session] = None
    ):
        self.username = username
        self.password = password
        self.s = session or requests.Session()
        # Default headers
        self.s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EasynewsClient/1.0",
                "Accept": "application/json, text/javascript, */*; q=0.9",
            }
        )
        # Use HTTP Basic Auth for endpoints that support it
        self.s.auth = (self.username, self.password)

    def login(self) -> None:
        """
        Prime session and validate credentials using a quick authenticated call.
        This relies on HTTP Basic Auth configured on the session.
        """
        def _do_login() -> requests.Response:
            self.s.get(f"{EASYNEWS_BASE}/2.0/", timeout=_LOGIN_TIMEOUT)
            return self.s.get(
                f"{EASYNEWS_BASE}/2.0/search/solr-search/?fly=2&gps=test&sb=1&pno=1&pby=1&u=1&chxu=1&chxgx=1&st=basic&s1=dtime&s1d=-&sS=3&vv=1&fty%5B%5D=VIDEO",
                allow_redirects=True,
                timeout=_LOGIN_TIMEOUT,
            )

        def _is_retryable(r: requests.Response) -> bool:
            return 500 <= r.status_code < 600

        try:
            resp = _retry(
                _do_login,
                max_retries=3,
                base_delay=1.0,
                on_retryable_response=_is_retryable,
            )
        except RequestException as e:
            logger.exception("Network error during Easynews login")
            raise EasynewsError(f"Network error during Easynews login: {e}") from e

        if resp.status_code in (401, 403):
            raise EasynewsError("Unauthorized; check username/password")

    def search(
        self,
        query: str,
        file_type: str = "VIDEO",
        page: int = 1,
        per_page: int = 50,
        sort_field: Optional[str] = "dtime",
        sort_dir: str = "-",
        safe_off: int = 0,
        max_retries: int = 3,
        stale_retry: bool = True,
    ) -> Dict[str, Any]:
        """
        Call the same Solr-backed endpoint used by the site.
        Returns the raw JSON dict, including data and pagination fields.

        Retry logic:
        - Retries on transient network errors (5xx, timeouts).
        - When *stale_retry* is True, re-fetches up to *max_retries* times
          if the result set is empty (possible with cached/stale indexes).
        """
        if file_type != "VIDEO":
            file_type = "VIDEO"

        params = {
            "fly": "2",
            "sb": "1",
            "pno": str(page),
            "pby": str(per_page),
            "u": "1",
            "chxu": "1",
            "chxgx": "1",
            "st": "basic",
            "gps": query,
            "vv": "1",
            "safeO": str(safe_off),
            # Cache-busting nonce to avoid serving stale cached results
            "_nonce": str(random.random()),
        }
        if sort_field:
            params["s1"] = sort_field
            params["s1d"] = sort_dir

        url = f"{EASYNEWS_BASE}/2.0/search/solr-search/"
        query_params = (
            "&".join([f"{k}={requests.utils.quote(v)}" for k, v in params.items()])
            + f"&fty%5B%5D={requests.utils.quote(file_type)}"
        )
        full_url = f"{url}?{query_params}"

        def _is_retryable(r: requests.Response) -> bool:
            return 500 <= r.status_code < 600

        def _do_search() -> Dict[str, Any]:
            r = self.s.get(full_url, timeout=_SEARCH_TIMEOUT)
            r.raise_for_status()
            return r.json()

        last_data: Optional[Dict[str, Any]] = None
        for attempt in range(max_retries + 1):
            try:
                data = _retry(
                    _do_search,
                    max_retries=2,
                    base_delay=1.0,
                    on_retryable_response=_is_retryable,
                )
            except RequestException as e:
                logger.exception("Search request failed for query '%s'", query)
                raise EasynewsError(f"Search request failed: {e}") from e

            # Stale result detection: if data array is empty and we're allowed to retry
            is_empty = not data.get("data")
            if is_empty and stale_retry and attempt < max_retries:
                delay = min(1.0 * (2 ** attempt) + random.uniform(0, 1.0), 15.0)
                logger.info(
                    "Empty results on attempt %d for '%s', re-fetching in %.1fs",
                    attempt + 1,
                    query,
                    delay,
                )
                # Regenerate nonce for the next attempt
                params["_nonce"] = str(random.random())
                query_params = (
                    "&".join([f"{k}={requests.utils.quote(v)}" for k, v in params.items()])
                    + f"&fty%5B%5D={requests.utils.quote(file_type)}"
                )
                full_url = f"{url}?{query_params}"
                time.sleep(delay)
                continue

            last_data = data
            break

        return last_data if last_data is not None else {}

    @staticmethod
    def _collect_items(json_data: Dict[str, Any]) -> List[SearchItem]:
        items: List[SearchItem] = []
        for it in json_data.get("data", []):
            hash_id = ""
            filename_no_ext = ""
            ext = ""
            sig: Optional[str] = None
            typ = ""
            item_id: Optional[str] = None

            if isinstance(it, list):
                if len(it) >= 12:
                    hash_id = it
                    filename_no_ext = it
                    ext = it
            elif isinstance(it, dict):
                if "0" in it:
                    hash_id = it.get("0", "")
                if "10" in it:
                    filename_no_ext = it.get("10", "")
                if "11" in it:
                    ext = it.get("11", "")
                sig = it.get("sig")
                typ = it.get("type", "")
                item_id = it.get("id")

            if not hash_id or not ext:
                continue

            items.append(
                SearchItem(
                    id=item_id,
                    hash=hash_id,
                    filename=filename_no_ext,
                    ext=ext,
                    sig=sig,
                    type=typ,
                    raw=it if isinstance(it, dict) else {},
                )
            )
        return items

    def build_nzb_payload(
        self,
        items: List[SearchItem],
        name: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Build the form-encoded payload expected by /2.0/api/dl-nzb.
        Emulates createNZB() from members.js which submits hidden inputs of the checked items.
        Keys look like "{index}&sig={sig}" and value is value_token.
        We'll just use sequential indexes starting at 0.
        """
        data: Dict[str, str] = {"autoNZB": "1"}
        for idx, it in enumerate(items):
            key = str(idx)
            if it.sig:
                key = f"{idx}&sig={it.sig}"
            data[key] = it.value_token
        if name:
            data["nameZipQ0"] = name
        return data

    def download_nzb(self, payload: Dict[str, str], out_path: str) -> str:
        url = f"{EASYNEWS_BASE}/2.0/api/dl-nzb"

        def _is_retryable(r: requests.Response) -> bool:
            return 500 <= r.status_code < 600

        def _do_download() -> requests.Response:
            return self.s.post(url, data=payload, stream=True, timeout=_DOWNLOAD_TIMEOUT)

        try:
            r = _retry(
                _do_download,
                max_retries=3,
                base_delay=2.0,
                on_retryable_response=_is_retryable,
            )
        except RequestException as e:
            logger.exception("NZB download request failed")
            raise EasynewsError(f"NZB download request failed: {e}") from e

        if r.status_code != 200:
            raise EasynewsError(f"NZB creation failed: HTTP {r.status_code}")

        content_type = r.headers.get("Content-Type", "")
        if "xml" not in content_type and "nzb" not in content_type:
            pass

        content = r.content.replace(b'date=""', b'date="0"')
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(content)
        return out_path

    def search_and_nzb(
        self,
        query: str,
        file_type: str = "VIDEO",
        max_items: int = 5,
        nzb_name: Optional[str] = None,
        out_path: str = "download.nzb",
    ) -> str:
        data = self.search(query=query, file_type=file_type)
        items = self._collect_items(data)
        if not items:
            raise EasynewsError("No results found for query")
        sel = items[:max_items]
        payload = self.build_nzb_payload(sel, name=nzb_name)
        return self.download_nzb(payload, out_path)


__all__ = ["EasynewsClient", "EasynewsError", "SearchItem"]
