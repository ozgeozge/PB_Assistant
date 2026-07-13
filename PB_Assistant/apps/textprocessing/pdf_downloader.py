
import os, requests, logging
from urllib.parse import urlparse
import ipaddress
import socket
import time
from django.conf import settings
from typing import List, Optional
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

class PdfDownloader:
    """
    Simple PDF downloader using a persistent Session with Retry/HTTPAdapter.
    - Retries on 408/429/5xx with exponential backoff
    - Keeps connections alive for faster repeated requests to same host
    - Optional Referer support (per-call)
    """

    def __init__(
        self,
        headers: Optional[dict] = None,
        save_dir: Optional[str] = None,
        timeout: int = 20,
        max_retries: int = 3,
        backoff_factor: float = 1.2,
        pool_connections: int = 4,
        pool_maxsize: int = 4,
        min_interval_seconds: Optional[float] = None,
    ):
        self.save_dir = save_dir or settings.PDF_PATH
        os.makedirs(self.save_dir, exist_ok=True)

        # Base headers; copied per request (we don’t mutate this dict)
        self.base_headers = {
            "User-Agent": (
                headers.get("User-Agent") if headers else
                "PB_Assistant/0.1 (research PDF downloader; contact: configure-contact@example.com)"
            )
        }
        if headers:
            for k, v in headers.items():
                if k.lower() != "user-agent":
                    self.base_headers[k] = v

        self.timeout = timeout
        self.min_interval_seconds = (
            min_interval_seconds
            if min_interval_seconds is not None
            else getattr(settings, "PDF_DOWNLOAD_MIN_INTERVAL_SECONDS", 1.0)
        )
        self._last_request_at_by_host = {}

        # One persistent Session with retry/backoff + connection pooling
        self.session = requests.Session()
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[408, 429, 500, 502, 503, 504],
            allowed_methods={"GET", "HEAD"},
            raise_on_status=False,
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _rate_limit(self, host: str) -> None:
        if self.min_interval_seconds <= 0:
            return

        now = time.monotonic()
        last_request_at = self._last_request_at_by_host.get(host)
        if last_request_at is not None:
            wait_for = self.min_interval_seconds - (now - last_request_at)
            if wait_for > 0:
                time.sleep(wait_for)

        self._last_request_at_by_host[host] = time.monotonic()

    def _is_safe_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            host = parsed.hostname
            if not host:
                return False
            if host in {"localhost", "127.0.0.1", "::1"}:
                return False
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return False
            except ValueError:
                for info in socket.getaddrinfo(host, None):
                    ip = ipaddress.ip_address(info[4][0])
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                        return False
        except Exception:
            return False
        return True

    def _is_pdf_response(self, response: requests.Response, first_chunk: bytes) -> bool:
        content_type = response.headers.get("Content-Type", "").lower()
        content_disposition = response.headers.get("Content-Disposition", "").lower()
        header = first_chunk[:1024].lstrip()

        if header.startswith(b"%PDF-"):
            return True

        # Some publishers use generic binary responses, but HTML error pages should not be saved.
        if "text/html" in content_type or header.startswith((b"<!doctype html", b"<html")):
            return False

        return "application/pdf" in content_type or ".pdf" in content_disposition

    def download(self, url: str, filename: str, referer: Optional[str] = None) -> bool:
        """
        GET the URL with retries, asking for PDF, and stream to disk.
        Returns True on success (HTTP 200), False otherwise.
        """
        path = os.path.join(self.save_dir, filename)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return True
        if not self._is_safe_url(url):
            logger.warning("Blocked unsafe URL: %s", url)
            return False
        host = urlparse(url).hostname
        if host:
            self._rate_limit(host)

        # Fresh headers per call
        headers = dict(self.base_headers)
        headers["Accept"] = "application/pdf"   # prefer PDF if server supports negotiation
        if referer:
            headers["Referer"] = referer

        try:
            r = self.session.get(
                url,
                stream=True,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=True,
            )
        except requests.RequestException as e:
            logger.debug(f"Request error for {url}: {e}")
            return False

        if r.status_code != 200:
            logger.debug(f"HTTP {r.status_code} for {url}")
            return False
        max_bytes = getattr(settings, "PDF_MAX_BYTES", 20 * 1024 * 1024)
        content_length = r.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            logger.warning("PDF too large (Content-Length=%s): %s", content_length, url)
            return False

        try:
            with open(path, "wb") as f:
                total = 0
                checked_pdf = False
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        if not checked_pdf:
                            checked_pdf = True
                            if not self._is_pdf_response(r, chunk):
                                logger.debug("Response does not look like a PDF: %s", url)
                                f.close()
                                os.remove(path)
                                return False
                        f.write(chunk)
                        total += len(chunk)
                        if total > max_bytes:
                            logger.warning("PDF exceeded max size while downloading: %s", url)
                            f.close()
                            os.remove(path)
                            return False
                if not checked_pdf:
                    logger.debug("Empty PDF response: %s", url)
                    os.remove(path)
                    return False
            return True
        except OSError as e:
            logger.debug(f"Write failed for {path}: {e}")
            return False

    def delete(self, pdf_filenames: List[str]) -> None:
        for filename in pdf_filenames:
            path = os.path.join(self.save_dir, filename)
            try:
                os.remove(path)
            except FileNotFoundError:
                logger.debug(f"File not found: {filename}")
            except Exception as e:
                logger.error(f"Error deleting PDF {filename}: {e!r}")


