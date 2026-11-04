"""Streamlit application for validating ads.txt and app-ads.txt entries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import DefaultCookiePolicy
import inspect
import ipaddress
import os
from pathlib import Path
import re
import socket
import time
from urllib.parse import urljoin, urlsplit

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
import streamlit as st
import urllib3
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import (
    ConnectTimeoutError,
    InsecureRequestWarning,
    NewConnectionError,
)
from urllib3.util import connection as urllib3_connection


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a bounded integer configuration value from an environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean configuration flag from an environment variable."""
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


# Operational and security limits (overridable via environment variables)
LIVE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
MAX_WORKERS = _env_int("ADS_CHECKER_MAX_WORKERS", default=16, minimum=1, maximum=64)
CONNECT_TIMEOUT = _env_int(
    "ADS_CHECKER_CONNECT_TIMEOUT", default=5, minimum=1, maximum=60
)
READ_TIMEOUT = _env_int("ADS_CHECKER_READ_TIMEOUT", default=10, minimum=1, maximum=120)
TOTAL_REQUEST_TIMEOUT = _env_int(
    "ADS_CHECKER_TOTAL_TIMEOUT", default=20, minimum=2, maximum=180
)
MAX_REDIRECTS = _env_int("ADS_CHECKER_MAX_REDIRECTS", default=5, minimum=0, maximum=10)
MAX_RESPONSE_BYTES = _env_int(
    "ADS_CHECKER_MAX_RESPONSE_BYTES",
    default=10 * 1024 * 1024,
    minimum=65536,
    maximum=50 * 1024 * 1024,
)
MAX_TARGETS = _env_int(
    "ADS_CHECKER_MAX_TARGETS", default=5000, minimum=1, maximum=50000
)
MAX_REFERENCES = _env_int(
    "ADS_CHECKER_MAX_REFERENCES", default=2000, minimum=1, maximum=20000
)
ALLOW_INSECURE_SSL_FALLBACK = _env_bool("ADS_CHECKER_ALLOW_INSECURE_SSL", default=True)
TRUST_ENV = _env_bool("ADS_CHECKER_TRUST_ENV", default=False)

ALLOWED_FILENAMES = frozenset({"ads.txt", "app-ads.txt"})
ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".arpa")
STREAM_CHUNK_SIZE = 65536
MAX_STYLER_CELLS = 262_144
PROGRESS_UPDATE_INTERVAL = 0.08

_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body")
_BLOCKED_MEDIA_PREFIXES = ("image/", "video/", "audio/")
_BLOCKED_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/zip",
        "application/gzip",
        "application/x-gzip",
    }
)

if ALLOW_INSECURE_SSL_FALLBACK:
    # Suppress per-request warning mutations when explicit SSL fallback is enabled.
    urllib3.disable_warnings(InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent
ICON_FILE = BASE_DIR / "icons" / "icon.png"
page_icon = str(ICON_FILE) if ICON_FILE.is_file() else None

_df_width_param = inspect.signature(st.dataframe).parameters.get("width")
_DATAFRAME_WIDTH_KWARGS = (
    {"width": "stretch"}
    if _df_width_param is not None and _df_width_param.default == "stretch"
    else {"use_container_width": True}
)


class UnsafeTargetError(ValueError):
    """Raised when a URL or host points to a restricted or non-public address."""


class ResponseTooLargeError(ValueError):
    """Raised when a remote HTTP response exceeds MAX_RESPONSE_BYTES."""


class _NoCookiesPolicy(DefaultCookiePolicy):
    """Cookie policy that discards all cookies for thread safety and privacy."""

    def set_ok(self, cookie, request):  # noqa: ANN001, D102
        return False

    def return_ok(self, cookie, request):  # noqa: ANN001, D102
        return False


def _is_public_ip(ip_str: str) -> bool:
    """Return True only if ``ip_str`` is a globally routable public IP address."""
    try:
        ip_obj = ipaddress.ip_address(ip_str)
    except ValueError:
        return False

    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped

    return (
        ip_obj.is_global
        and not ip_obj.is_private
        and not ip_obj.is_loopback
        and not ip_obj.is_link_local
        and not ip_obj.is_multicast
        and not ip_obj.is_reserved
        and not ip_obj.is_unspecified
    )


def _is_valid_target_host(hostname: str) -> bool:
    """Return True if ``hostname`` is a valid public domain or public IP literal."""
    if not hostname or len(hostname) > 253:
        return False

    host = hostname.strip("[]").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(BLOCKED_HOST_SUFFIXES):
        return False

    # Check canonical IPv4 / IPv6 literals directly.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _is_public_ip(host)

    # Reject single-label intranet names and numeric IP encodings (e.g. 0x7f000001).
    if "." not in host:
        return False

    labels = host.split(".")
    tld = labels[-1]
    if len(tld) < 2 or tld.isdigit():
        return False

    return all(_LABEL_RE.match(label) is not None for label in labels)


def _create_safe_socket(
    host: str,
    port: int,
    timeout: float | None,
    source_address: tuple[str, int] | None = None,
    socket_options: list[tuple[int, int, int | bytes]] | None = None,
) -> socket.socket:
    """Resolve DNS once, verify all candidate IPs are public, and connect directly.

    Connecting directly to the validated socket address prevents DNS-rebinding
    (TOCTOU) attacks between hostname resolution and TCP connection setup.
    """
    if port not in ALLOWED_PORTS:
        raise UnsafeTargetError(f"Blocked disallowed port: {port}")

    clean_host = host.strip("[]")
    if not _is_valid_target_host(clean_host):
        raise UnsafeTargetError("Blocked invalid or non-public target host")

    addrinfos = socket.getaddrinfo(
        clean_host, port, socket.AF_UNSPEC, socket.SOCK_STREAM
    )
    if not addrinfos:
        raise OSError(f"No network address found for {clean_host}")

    safe_targets = []
    for family, socktype, proto, canonname, sockaddr in addrinfos:
        ip_str = sockaddr[0]
        if not _is_public_ip(ip_str):
            raise UnsafeTargetError(f"Blocked non-public IP address ({ip_str})")
        safe_targets.append((family, socktype, proto, canonname, sockaddr))

    last_err: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in safe_targets:
        sock: socket.socket | None = None
        try:
            sock = socket.socket(family, socktype, proto)
            if socket_options:
                urllib3_connection._set_socket_options(sock, socket_options)
            if timeout is not None and timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_err = exc
            if sock is not None:
                sock.close()

    if last_err is not None:
        raise last_err
    raise OSError(f"Unable to connect to {clean_host}")


class _SafeHTTPConnection(HTTPConnection):
    """HTTPConnection subclass that enforces public-IP-only socket connections."""

    def _new_conn(self) -> socket.socket:
        try:
            return _create_safe_socket(
                self._dns_host,
                self.port,
                self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except UnsafeTargetError as exc:
            raise NewConnectionError(self, str(exc)) from exc
        except socket.timeout as exc:
            raise ConnectTimeoutError(
                self,
                f"Connection to {self.host} timed out.",
            ) from exc
        except OSError as exc:
            raise NewConnectionError(
                self,
                f"Failed to establish a connection: {exc}",
            ) from exc


class _SafeHTTPSConnection(_SafeHTTPConnection, HTTPSConnection):
    """HTTPSConnection subclass that pairs public-IP TCP sockets with full TLS SNI."""


class _SafeHTTPConnectionPool(HTTPConnectionPool):
    ConnectionCls = _SafeHTTPConnection


class _SafeHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = _SafeHTTPSConnection


class _SafeHTTPAdapter(HTTPAdapter):
    """Requests HTTPAdapter with SSRF-hardened connection pools."""

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):  # noqa: ANN001
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": _SafeHTTPConnectionPool,
            "https": _SafeHTTPSConnectionPool,
        }


def get_session() -> requests.Session:
    """Create a thread-safe, SSRF-hardened HTTP session for ads.txt retrieval.

    Returns:
        requests.Session: A session with browser-like headers, disabled cookie
            storage, connection pooling, and public-IP socket enforcement.

    Examples:
        >>> session = get_session()
        >>> session.headers["User-Agent"] == LIVE_UA
        True
    """
    session = requests.Session()
    session.trust_env = TRUST_ENV
    session.cookies.set_policy(_NoCookiesPolicy())
    session.headers.update(
        {
            "User-Agent": LIVE_UA,
            "Accept": (
                "text/plain,text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
        }
    )
    pool_size = max(16, MAX_WORKERS * 2)
    adapter = _SafeHTTPAdapter(
        pool_connections=pool_size, pool_maxsize=pool_size, max_retries=0
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_clean_domain(url_input: str) -> str:
    """Normalize a user-provided URL or hostname into a clean domain string.

    Args:
        url_input (str): A hostname or URL entered by the user. If the value
            omits a scheme, ``http://`` is assumed during parsing.

    Returns:
        str: Lowercased ASCII/punycode hostname (or bracketed IPv6 literal), or
            an empty string when the input contains a disallowed scheme,
            embedded credentials, disallowed port, or invalid syntax.

    Examples:
        >>> get_clean_domain("example.com")
        'example.com'
        >>> get_clean_domain("https://sub.example.com/path")
        'sub.example.com'
    """
    if not isinstance(url_input, str):
        return ""

    cleaned = url_input.strip().strip("'\"")
    if not cleaned or any(ch.isspace() or ord(ch) < 32 for ch in cleaned):
        return ""

    # Support bare IPv6 literals passed without brackets or scheme.
    try:
        bare_ip = ipaddress.ip_address(cleaned)
        if isinstance(bare_ip, ipaddress.IPv6Address):
            return f"[{bare_ip.compressed}]"
        return str(bare_ip)
    except ValueError:
        pass

    if "://" in cleaned:
        scheme_prefix = cleaned.split("://", 1)[0].lower()
        if scheme_prefix not in ALLOWED_SCHEMES:
            return ""
        candidate = cleaned
    else:
        candidate = "http://" + cleaned.lstrip("/")

    try:
        parsed = urlsplit(candidate)
        if parsed.username is not None or parsed.password is not None:
            return ""
        port = parsed.port
        if port is not None and port not in ALLOWED_PORTS:
            return ""
        hostname = parsed.hostname
    except ValueError:
        return ""

    if not hostname:
        return ""

    hostname = hostname.rstrip(".").lower()
    if not hostname:
        return ""

    # Preserve bracket notation for valid IPv6 literals, otherwise IDNA-encode domains.
    try:
        ip_obj = ipaddress.ip_address(hostname)
        if isinstance(ip_obj, ipaddress.IPv6Address):
            return f"[{ip_obj.compressed}]"
        return str(ip_obj)
    except ValueError:
        pass

    try:
        ascii_host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return ""

    return ascii_host


def _validate_request_url(url: str) -> None:
    """Validate that a full request or redirect URL uses safe scheme, host, and port."""
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise UnsafeTargetError("Malformed target URL") from exc

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeTargetError(f"Unsupported URL scheme: {parsed.scheme}")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeTargetError("Embedded URL credentials are not allowed")

    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeTargetError("Invalid port in URL") from exc

    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeTargetError(f"Disallowed target port: {port}")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeTargetError("Missing target hostname")

    try:
        ascii_host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        # IPv6 addresses do not need IDNA encoding.
        ascii_host = hostname.lower()
        try:
            ipaddress.ip_address(ascii_host)
        except ValueError:
            raise UnsafeTargetError("Invalid hostname encoding") from exc

    if not _is_valid_target_host(ascii_host):
        raise UnsafeTargetError("Blocked invalid or non-public target host")


def _looks_like_html_or_binary(head_bytes: bytes) -> tuple[bool, str]:
    """Inspect initial response bytes to reject binary payloads and HTML soft-404s."""
    if b"\x00" in head_bytes:
        return True, "Error: Binary file instead of txt"

    stripped = head_bytes.lstrip().lower()
    if (
        any(stripped.startswith(marker) for marker in _HTML_MARKERS)
        or b"<!doctype html" in stripped
    ):
        return True, "Error: HTML Page instead of txt"

    return False, ""


def _read_bounded_response(
    response: requests.Response, deadline: float
) -> tuple[str | None, str, bool]:
    """Stream and validate an HTTP 200 response within size and wall-clock limits."""
    content_type = (
        (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    )
    if (
        content_type.startswith(_BLOCKED_MEDIA_PREFIXES)
        or content_type in _BLOCKED_CONTENT_TYPES
    ):
        return None, f"Error: Unexpected content type ({content_type})", True

    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_RESPONSE_BYTES:
                return None, "Error: File exceeds size limit", True
        except ValueError:
            pass

    # Support mocked responses in tests that set ``response.text`` without ``raw``.
    iter_content_fn = getattr(response, "iter_content", None)
    raw_attr = getattr(response, "raw", None)
    if raw_attr is None and isinstance(getattr(response, "text", None), str):
        sample = response.text[:2048].encode("utf-8", errors="replace")
        is_bad, err_msg = _looks_like_html_or_binary(sample)
        if is_bad:
            return None, err_msg, True
        if len(response.text.encode("utf-8", errors="replace")) > MAX_RESPONSE_BYTES:
            return None, "Error: File exceeds size limit", True
        return response.text, "OK", False

    chunks: list[bytes] = []
    total_bytes = 0
    checked_header = False

    for chunk in iter_content_fn(chunk_size=STREAM_CHUNK_SIZE):
        if time.monotonic() > deadline:
            raise requests.exceptions.ReadTimeout("Response read timed out")
        if not chunk:
            continue

        total_bytes += len(chunk)
        if total_bytes > MAX_RESPONSE_BYTES:
            raise ResponseTooLargeError("Response body exceeds size limit")

        chunks.append(chunk)
        if not checked_header and total_bytes >= 512:
            checked_header = True
            is_bad, err_msg = _looks_like_html_or_binary(b"".join(chunks)[:2048])
            if is_bad:
                return None, err_msg, True

    raw_bytes = b"".join(chunks)
    if not checked_header and raw_bytes:
        is_bad, err_msg = _looks_like_html_or_binary(raw_bytes[:2048])
        if is_bad:
            return None, err_msg, True

    return raw_bytes.decode("utf-8-sig", errors="replace"), "OK", False


def _fetch_single_url(
    session: requests.Session,
    start_url: str,
    verify_ssl: bool,
) -> tuple[str | None, str, bool]:
    """Fetch a URL across validated redirects within size and time bounds."""
    deadline = time.monotonic() + TOTAL_REQUEST_TIMEOUT
    current_url = start_url

    for hop in range(MAX_REDIRECTS + 1):
        _validate_request_url(current_url)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.exceptions.Timeout("Request timed out")

        timeout_tuple = (
            min(float(CONNECT_TIMEOUT), remaining),
            min(float(READ_TIMEOUT), remaining),
        )

        response = session.get(
            current_url,
            timeout=timeout_tuple,
            allow_redirects=False,
            verify=verify_ssl,
            stream=True,
        )
        try:
            status_code = response.status_code
            if status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    return None, f"HTTP {status_code} (missing redirect Location)", True
                if hop >= MAX_REDIRECTS:
                    return None, "Too many redirects", True
                current_url = urljoin(current_url, location.strip())
                continue

            if status_code == 200:
                return _read_bounded_response(response, deadline)

            return None, f"HTTP {status_code}", True
        finally:
            close_fn = getattr(response, "close", None)
            if callable(close_fn):
                close_fn()

    return None, "Too many redirects", True


def _contains_unsafe_target_error(exc: BaseException) -> bool:
    """Walk exception cause/context chain to detect SSRF block exceptions."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, UnsafeTargetError) or "Blocked " in str(current):
            return True
        args = getattr(current, "args", ())
        for arg in args:
            if isinstance(arg, BaseException) and _contains_unsafe_target_error(arg):
                return True
        current = current.__cause__ or current.__context__
    return False


def _format_request_error(exc: BaseException) -> str:
    """Translate network exceptions into concise, non-leaky diagnostic messages."""
    if _contains_unsafe_target_error(exc):
        return "Blocked non-public or restricted host"
    if isinstance(exc, ResponseTooLargeError):
        return "File exceeds size limit"
    if isinstance(exc, requests.exceptions.Timeout):
        return "Connection timed out"
    if isinstance(exc, requests.exceptions.TooManyRedirects):
        return "Too many redirects"
    if isinstance(exc, requests.exceptions.SSLError):
        return "SSL certificate verification failed"
    if isinstance(exc, requests.exceptions.ConnectionError):
        msg = str(exc).lower()
        if (
            "nameresolutionerror" in msg
            or "name or service not known" in msg
            or "nodename nor servname" in msg
            or "getaddrinfo failed" in msg
        ):
            return "DNS resolution failed"
        if "connection refused" in msg:
            return "Connection refused"
        if "connection reset" in msg:
            return "Connection reset by peer"
        return "Connection failed"
    if isinstance(exc, requests.exceptions.RequestException):
        return "HTTP request error"
    return "Unexpected fetch error"


def fetch_file_content(
    domain: str,
    filename: str,
    session: requests.Session | None = None,
) -> tuple[str | None, str, bool]:
    """Fetch an ads.txt or app-ads.txt file from a target domain.

    Args:
        domain (str): The raw target website or hostname to query.
        filename (str): The file name to retrieve (``ads.txt`` or ``app-ads.txt``).
        session (requests.Session | None): Optional shared session for connection
            reuse across batch jobs. When omitted, a temporary session is created
            and closed automatically.

    Returns:
        tuple[str | None, str, bool]: Fetched text content (or ``None`` on error),
        a human-readable status message, and a boolean error flag.

    Examples:
        >>> content, status, is_error = fetch_file_content("127.0.0.1", "ads.txt")
        >>> is_error and content is None
        True
    """
    if filename not in ALLOWED_FILENAMES:
        return None, "Not accessible: Invalid file type", True

    domain_clean = get_clean_domain(domain)
    if not domain_clean or not _is_valid_target_host(domain_clean):
        return None, "Not accessible: Invalid or restricted domain", True

    urls = (
        f"https://{domain_clean}/{filename}",
        f"http://{domain_clean}/{filename}",
    )

    owns_session = session is None
    active_session = get_session() if owns_session else session
    last_error = "Unknown error"

    try:
        for url in urls:
            try:
                text, status_msg, is_error = _fetch_single_url(
                    active_session, url, verify_ssl=True
                )
                if not is_error:
                    return text, "OK", False
                if status_msg.startswith("Error: "):
                    return None, status_msg, True
                last_error = status_msg
            except UnsafeTargetError as exc:
                return None, f"Not accessible: {_format_request_error(exc)}", True
            except requests.exceptions.SSLError as ssl_exc:
                last_error = _format_request_error(ssl_exc)
                if not ALLOW_INSECURE_SSL_FALLBACK:
                    continue
                try:
                    text, status_msg, is_error = _fetch_single_url(
                        active_session,
                        url,
                        verify_ssl=False,
                    )
                    if not is_error:
                        return text, "OK (SSL Warning)", False
                    if status_msg.startswith("Error: "):
                        return None, status_msg, True
                    last_error = f"{status_msg} (SSL Warning)"
                except Exception as retry_exc:
                    if _contains_unsafe_target_error(retry_exc):
                        return (
                            None,
                            f"Not accessible: {_format_request_error(retry_exc)}",
                            True,
                        )
                    last_error = _format_request_error(retry_exc)
            except Exception as exc:
                if _contains_unsafe_target_error(exc):
                    return None, f"Not accessible: {_format_request_error(exc)}", True
                last_error = _format_request_error(exc)
    finally:
        if owns_session:
            active_session.close()

    return None, f"Not accessible: {last_error}", True


def _sanitize_relationship_token(token: str) -> str:
    """Bound and strip control characters from an untrusted relationship token."""
    cleaned = "".join(ch for ch in token if ch.isprintable() and ch not in "\r\n\t")
    return cleaned[:64]


def _iter_parsed_ads_entries(content: str | None):
    """Yield normalized ``(domain, id, type)`` tuples from ads.txt content."""
    if not content:
        return

    normalized_content = content.lstrip("\ufeff")
    for line in normalized_content.splitlines():
        clean_line = line.partition("#")[0].strip()
        if not clean_line:
            continue

        # Skip IAB variable declarations such as CONTACT=, SUBDOMAIN=, OWNERDOMAIN=.
        eq_pos = clean_line.find("=")
        if eq_pos != -1:
            comma_pos = clean_line.find(",")
            if comma_pos == -1 or eq_pos < comma_pos:
                continue

        parts = clean_line.split(",")
        if len(parts) < 2:
            continue

        ad_domain = parts[0].strip().lower()
        seller_id = parts[1].strip().lower()
        if not ad_domain or not seller_id:
            continue

        rel_type: str | None = None
        if len(parts) > 2:
            raw_type = _sanitize_relationship_token(parts[2].strip().upper())
            if raw_type:
                rel_type = raw_type

        yield ad_domain, seller_id, rel_type


def parse_ads_file(content: str | None) -> list[dict[str, str | None]]:
    """Parse ads.txt or app-ads.txt content into structured records.

    Args:
        content (str | None): Raw file content returned from a remote target.
            Empty values produce an empty result list.

    Returns:
        list[dict[str, str | None]]: Parsed records containing the advertising
        system domain, seller account ID, and optional relationship type.

    Examples:
        >>> parse_ads_file("exchange.com, pub-123, DIRECT\\n")
        [{'domain': 'exchange.com', 'id': 'pub-123', 'type': 'DIRECT'}]
    """
    return [
        {"domain": ad_domain, "id": seller_id, "type": rel_type}
        for ad_domain, seller_id, rel_type in _iter_parsed_ads_entries(content)
    ]


def _index_ads_file(content: str | None) -> dict[tuple[str, str], list[str | None]]:
    """Build an O(1) index mapping ``(domain, id)`` to observed relationship types."""
    index: dict[tuple[str, str], list[str | None]] = {}
    for ad_domain, seller_id, rel_type in _iter_parsed_ads_entries(content):
        index.setdefault((ad_domain, seller_id), []).append(rel_type)
    return index


def parse_reference_line(line: str) -> dict[str, str | None] | None:
    """Parse a user-supplied reference rule into a normalized comparable record.

    Args:
        line (str): A comma-separated reference line containing at least a
            domain and seller ID, with an optional relationship type.

    Returns:
        dict[str, str | None] | None: A normalized reference dictionary when
        valid; otherwise ``None``.

    Examples:
        >>> parse_reference_line("onetag.com, 5d0d72448d8bfb0, DIRECT")['type']
        'DIRECT'
        >>> parse_reference_line("invalid") is None
        True
    """
    if not isinstance(line, str):
        return None

    clean_line = line.lstrip("\ufeff").partition("#")[0].strip()
    if not clean_line:
        return None

    eq_pos = clean_line.find("=")
    if eq_pos != -1:
        comma_pos = clean_line.find(",")
        if comma_pos == -1 or eq_pos < comma_pos:
            return None

    parts = [p.strip() for p in clean_line.split(",")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None

    rel_type: str | None = None
    if len(parts) > 2 and parts[2]:
        sanitized = _sanitize_relationship_token(parts[2].upper())
        rel_type = sanitized if sanitized else None

    return {
        "domain": parts[0].lower(),
        "id": parts[1].lower(),
        "type": rel_type,
        "original": clean_line,
    }


def _evaluate_references_for_target(
    target_url: str,
    filename: str,
    references: list[dict[str, str | None]],
    records_index: dict[tuple[str, str], list[str | None]] | None,
    status_msg: str,
    is_error: bool,
) -> list[dict[str, str]]:
    """Evaluate parsed reference rules against an indexed target domain."""
    if is_error or records_index is None:
        return [
            {
                "URL": target_url,
                "File": filename,
                "Result": "Error",
                "Details": status_msg,
                "Reference": str(ref["original"]),
            }
            for ref in references
        ]

    results: list[dict[str, str]] = []
    for ref in references:
        ref_domain = str(ref["domain"])
        ref_id = str(ref["id"])
        ref_type = ref["type"]
        observed_types = records_index.get((ref_domain, ref_id))

        if observed_types is None:
            match_status = "Not found"
            details = "No matching Domain+ID pair"
        elif not ref_type:
            match_status = "Valid"
            details = "Matched by Domain + ID"
        elif ref_type in observed_types:
            match_status = "Valid"
            details = "Full match"
        else:
            match_status = "Partially matched"
            details = f"Type mismatch: found {observed_types[-1]}, expected {ref_type}"

        results.append(
            {
                "URL": target_url,
                "File": filename,
                "Result": match_status,
                "Details": details,
                "Reference": str(ref["original"]),
            }
        )

    return results


def _fetch_and_index_domain(
    domain: str,
    filename: str,
    session: requests.Session | None = None,
) -> tuple[dict[tuple[str, str], list[str | None]] | None, str, bool]:
    """Fetch and index a single normalized domain for reuse across matching URLs."""
    content, status_msg, is_error = fetch_file_content(
        domain, filename, session=session
    )
    if is_error:
        return None, status_msg, True
    return _index_ads_file(content), status_msg, False


def validate_domain(
    target_domain: str,
    filename: str,
    references: list[dict[str, str | None]],
    session: requests.Session | None = None,
) -> list[dict[str, str]]:
    """Validate a target domain against one or more reference entries.

    Args:
        target_domain (str): The website whose ``ads.txt`` or ``app-ads.txt``
            file should be checked.
        filename (str): The file name to validate (``ads.txt`` or ``app-ads.txt``).
        references (list[dict[str, str | None]]): Parsed reference entries to
            match against the remote file content.
        session (requests.Session | None): Optional shared HTTP session.

    Returns:
        list[dict[str, str]]: Result rows for each reference entry.

    Examples:
        >>> refs = [{
        ...     'domain': 'exchange.com',
        ...     'id': 'pub-123',
        ...     'type': 'DIRECT',
        ...     'original': 'exchange.com, pub-123, DIRECT',
        ... }]
        >>> isinstance(validate_domain('127.0.0.1', 'ads.txt', refs), list)
        True
    """
    records_index, status_msg, is_error = _fetch_and_index_domain(
        target_domain,
        filename,
        session=session,
    )
    return _evaluate_references_for_target(
        target_domain,
        filename,
        references,
        records_index,
        status_msg,
        is_error,
    )


def classify_error(row: pd.Series | dict[str, str]) -> str:
    """Map a single result row to its UI error-filter category.

    Args:
        row (pd.Series | dict[str, str]): A row containing ``Result`` and
            ``Details`` fields.

    Returns:
        str: Display category used by the error filter multiselect.

    Examples:
        >>> classify_error(pd.Series({'Result': 'Valid', 'Details': 'Full match'}))
        'Valid'
    """
    result_val = str(row["Result"])
    details_val = str(row["Details"])

    if result_val == "Valid":
        return "Valid"
    if "found RESELLER, expected DIRECT" in details_val:
        return "Type mismatch: found RESELLER, expected DIRECT"
    if "No matching Domain+ID pair" in details_val:
        return "Not Found (No Domain+ID)"
    if result_val == "Partially matched":
        return "Other Partial Matches"
    if result_val == "Error" or "System Error" in result_val or "HTTP" in details_val:
        return "Connection / System Errors"
    return "Other"


def _classify_errors_vectorized(df: pd.DataFrame) -> pd.Series:
    """Vectorized equivalent of :func:`classify_error` for fast DataFrame filtering."""
    res = df["Result"].astype(str)
    det = df["Details"].astype(str)

    categories = pd.Series("Other", index=df.index, dtype="object")
    is_conn = (
        (res == "Error")
        | res.str.contains("System Error", regex=False)
        | det.str.contains("HTTP", regex=False)
    )
    is_partial = res == "Partially matched"
    is_not_found = det.str.contains("No matching Domain+ID pair", regex=False)
    is_reseller_mismatch = det.str.contains(
        "found RESELLER, expected DIRECT", regex=False
    )
    is_valid = res == "Valid"

    categories[is_conn] = "Connection / System Errors"
    categories[is_partial] = "Other Partial Matches"
    categories[is_not_found] = "Not Found (No Domain+ID)"
    categories[is_reseller_mismatch] = "Type mismatch: found RESELLER, expected DIRECT"
    categories[is_valid] = "Valid"
    return categories


def color_status(val: object) -> str:
    """Return a CSS style declaration for a result status cell.

    Args:
        val (object): Status string displayed in the output table.

    Returns:
        str: CSS declaration string used by pandas Styler.

    Examples:
        >>> color_status('Valid')
        'background-color: #21aeb3; color: white'
    """
    if val == "Valid":
        return "background-color: #21aeb3; color: white"
    if val == "Partially matched":
        return "background-color: #000000; color: #21aeb3; font-weight: bold"
    if val == "Not found":
        return "background-color: #383838; color: #aaaaaa"
    if val in {"Error", "System Error"}:
        return "background-color: #2d2d2d; color: #888888"
    return ""


def _sanitize_csv_cell(value: object) -> object:
    """Neutralize spreadsheet formula injection payloads in exported CSV cells."""
    if not isinstance(value, str) or not value or value == "-":
        return value
    if value[0] in ("=", "+", "-", "@", "\t", "\r") or value.lstrip(
        " \t\r\n"
    ).startswith(("=", "+", "@")):
        return f"'{value}"
    return value


def _prepare_safe_csv_bytes(df: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to UTF-8-SIG CSV bytes safe from formula injection."""
    safe_df = df.copy()
    for col in safe_df.columns:
        if pd.api.types.is_string_dtype(safe_df[col]) or safe_df[col].dtype == object:
            safe_df[col] = safe_df[col].map(_sanitize_csv_cell)
    return safe_df.to_csv(index=False).encode("utf-8-sig")


def _style_results_dataframe(df: pd.DataFrame, subset_cols: list[str]):
    """Apply status cell coloring when within safe Styler cell limits."""
    total_cells = df.shape[0] * df.shape[1]
    if not subset_cols or total_cells > MAX_STYLER_CELLS:
        return df

    styler = df.style
    map_fn = getattr(styler, "map", None)
    if callable(map_fn):
        return map_fn(color_status, subset=subset_cols)
    return styler.applymap(color_status, subset=subset_cols)


def _run_batch_fetches(
    unique_domain_keys: list[str],
    file_type: str,
    progress_bar,
    status_text,
) -> dict[str, tuple[dict[tuple[str, str], list[str | None]] | None, str, bool, bool]]:
    """Fetch and index unique domains concurrently while updating UI progress."""
    domain_outcomes: dict[
        str,
        tuple[dict[tuple[str, str], list[str | None]] | None, str, bool, bool],
    ] = {}

    total_domains = len(unique_domain_keys)
    workers = min(MAX_WORKERS, max(1, total_domains))
    last_ui_update = 0.0

    with get_session() as session_pool, ThreadPoolExecutor(workers) as pool:
        future_to_key = {
            pool.submit(
                _fetch_and_index_domain,
                domain_key,
                file_type,
                session_pool,
            ): domain_key
            for domain_key in unique_domain_keys
            if not domain_key.startswith("__invalid__:")
        }

        completed_count = 0
        for domain_key in unique_domain_keys:
            if domain_key.startswith("__invalid__:"):
                domain_outcomes[domain_key] = (
                    None,
                    "Not accessible: Invalid or restricted domain",
                    True,
                    False,
                )
                completed_count += 1

        for future in as_completed(future_to_key):
            domain_key = future_to_key[future]
            try:
                records_index, status_msg, is_error = future.result()
                domain_outcomes[domain_key] = (
                    records_index,
                    status_msg,
                    is_error,
                    False,
                )
            except Exception as exc:
                domain_outcomes[domain_key] = (
                    None,
                    _format_request_error(exc),
                    True,
                    True,
                )

            completed_count += 1
            now = time.monotonic()
            if (
                completed_count == total_domains
                or (now - last_ui_update) >= PROGRESS_UPDATE_INTERVAL
            ):
                last_ui_update = now
                progress_bar.progress(completed_count / total_domains)
                status_text.text(
                    f"Checked ({completed_count}/{total_domains}): {domain_key}"
                )

    return domain_outcomes


def _is_running_in_streamlit() -> bool:
    """Return True when executing inside an active Streamlit runtime context."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except ImportError:
        return False
    try:
        return get_script_run_ctx(suppress_warning=True) is not None
    except TypeError:
        return get_script_run_ctx() is not None


CUSTOM_DARK_CSS = """
    <style>
    .stApp {
        background-color: #1e1e1e;
        color: #e0e0e0;
    }

    h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        color: #ffffff !important;
    }

    h3 {
        padding-top: 10px !important;
        padding-bottom: 5px !important;
    }

    .stTextArea textarea {
        background-color: #2d2d2d !important;
        color: #ffffff !important;
        border: 1px solid #444 !important;
    }
    .stTextArea textarea:focus {
        border-color: #21aeb3 !important;
        box-shadow: 0 0 0 1px #21aeb3 !important;
    }

    div.stButton > button {
        background-color: #21aeb3 !important;
        color: white !important;
        border: none !important;
        font-weight: bold !important;
        transition: 0.3s;
    }
    div.stButton > button:hover {
        background-color: #1a8a8e !important;
        border: 1px solid #ffffff !important;
    }

    div[data-baseweb="notification"] {
        background-color: #2d2d2d !important;
        border: 1px solid #21aeb3 !important;
        color: #ffffff !important;
    }
    div[data-baseweb="notification"] svg {
        fill: #21aeb3 !important;
    }

    div[data-testid="stDataFrame"] {
        background-color: #2d2d2d;
    }

    .stRadio label, .stMultiSelect label {
        color: #e0e0e0 !important;
    }
    div[role="radiogroup"] div[aria-checked="true"] div:first-child {
        background-color: #21aeb3 !important;
        border-color: #21aeb3 !important;
    }
    div[role="radiogroup"] > div {
        color: #e0e0e0 !important;
    }

    .stProgress > div > div > div > div {
        background-color: #21aeb3 !important;
    }

    .stRadio {
        margin-top: 0px !important;
        margin-bottom: 0px !important;
    }

    div[data-testid="column"] > div > div {
        gap: 0.5rem !important;
    }

    .compact-hr {
        margin: 10px 0 !important;
        border: 0;
        border-top: 1px solid #444;
    }

    [data-testid="stImage"] {
        margin-top: 15px;
    }

    .stMultiSelect span {
        color: #e0e0e0 !important;
    }
    </style>
"""


def render_app() -> None:
    """Render the interactive Streamlit application UI."""
    st.set_page_config(
        page_title="Ads.txt / App-ads.txt line Checker",
        layout="wide",
        page_icon=page_icon,
    )
    st.markdown(CUSTOM_DARK_CSS, unsafe_allow_html=True)

    col_head1, col_head2 = st.columns([0.6, 12])
    with col_head1:
        if ICON_FILE.is_file():
            st.image(str(ICON_FILE), width=65)
    with col_head2:
        st.title("Ads.txt / App-ads.txt line Checker")

    if "results_df" not in st.session_state:
        st.session_state.results_df = None
    if "all_results_raw" not in st.session_state:
        st.session_state.all_results_raw = None
    if "processed_refs" not in st.session_state:
        st.session_state.processed_refs = []
    if "target_order" not in st.session_state:
        st.session_state.target_order = []
    if "checked_file_type" not in st.session_state:
        st.session_state.checked_file_type = "app-ads.txt"

    st.subheader("Settings")
    set_col1, set_col2 = st.columns(2)

    with set_col1:
        file_type = st.radio(
            "File Type",
            ("app-ads.txt", "ads.txt"),
        )

        st.markdown('<div class="compact-hr"></div>', unsafe_allow_html=True)

        view_mode = st.radio(
            "Output View",
            ("Show All Results", "Errors / Warnings Only"),
            index=1,
        )

    with set_col2:
        layout_mode = st.radio(
            "Result Layout",
            ("Standard (Vertical)", "Horizontal (Aggregated)"),
            index=0,
        )

        error_filters: list[str] = []
        if view_mode == "Errors / Warnings Only":
            st.markdown('<div class="compact-hr"></div>', unsafe_allow_html=True)

            err_options = [
                "Type mismatch: found RESELLER, expected DIRECT",
                "Not Found (No Domain+ID)",
                "Other Partial Matches",
                "Connection / System Errors",
            ]

            error_filters = st.multiselect(
                "Filter specific errors:",
                options=err_options,
                default=[
                    "Not Found (No Domain+ID)",
                    "Other Partial Matches",
                    "Connection / System Errors",
                ],
            )

    st.markdown(
        '<div class="compact-hr" style="margin: 15px 0 !important;"></div>',
        unsafe_allow_html=True,
    )

    st.subheader("Input Data")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Target Websites**")
        target_input = st.text_area(
            "Label hidden",
            height=300,
            placeholder="example.com\nmygame.site",
            label_visibility="collapsed",
        )

    with col2:
        st.markdown("**Reference Lines (Rules)**")
        ref_input = st.text_area(
            "Label hidden",
            height=300,
            placeholder="onetag.com, 5d0d72448d8bfb0, DIRECT",
            label_visibility="collapsed",
        )

    start_btn = st.button("Start Validation")

    if start_btn:
        if not target_input.strip() or not ref_input.strip():
            st.warning("Please fill both windows.")
        else:
            # Preserve input sequence while eliminating exact duplicate target lines.
            targets = list(
                dict.fromkeys(t.strip() for t in target_input.splitlines() if t.strip())
            )
            if not targets:
                st.warning("No valid target websites found.")
                st.stop()

            if len(targets) > MAX_TARGETS:
                st.warning(
                    f"Target list exceeds the maximum batch limit ({MAX_TARGETS}). "
                    f"Processing the first {MAX_TARGETS} unique targets."
                )
                targets = targets[:MAX_TARGETS]

            references: list[dict[str, str | None]] = []
            seen_ref_keys: set[tuple[str, str, str | None]] = set()
            for raw_ref in ref_input.splitlines():
                stripped_ref = raw_ref.strip()
                if not stripped_ref:
                    continue
                parsed_ref = parse_reference_line(stripped_ref)
                if parsed_ref is None:
                    continue
                ref_key = (
                    str(parsed_ref["domain"]),
                    str(parsed_ref["id"]),
                    parsed_ref["type"],
                )
                if ref_key not in seen_ref_keys:
                    seen_ref_keys.add(ref_key)
                    references.append(parsed_ref)

            if not references:
                st.warning("No valid reference lines found.")
                st.stop()

            if len(references) > MAX_REFERENCES:
                st.warning(
                    f"Reference list exceeds the maximum limit ({MAX_REFERENCES}). "
                    f"Using the first {MAX_REFERENCES} unique rules."
                )
                references = references[:MAX_REFERENCES]

            st.session_state.target_order = targets
            st.session_state.processed_refs = references
            st.session_state.checked_file_type = file_type

            # Group targets by normalized domain so shared hosts are fetched once.
            target_to_domain_key: dict[str, str] = {}
            unique_domain_keys: list[str] = []
            seen_domain_keys: set[str] = set()

            for target_url in targets:
                clean_dom = get_clean_domain(target_url)
                domain_key = clean_dom if clean_dom else f"__invalid__:{target_url}"
                target_to_domain_key[target_url] = domain_key
                if domain_key not in seen_domain_keys:
                    seen_domain_keys.add(domain_key)
                    unique_domain_keys.append(domain_key)

            progress_bar = st.progress(0)
            status_text = st.empty()
            domain_outcomes = _run_batch_fetches(
                unique_domain_keys,
                file_type,
                progress_bar,
                status_text,
            )
            progress_bar.empty()
            status_text.empty()

            # Assemble results in the exact input order of targets and references.
            all_results: list[dict[str, str]] = []
            for target_url in targets:
                domain_key = target_to_domain_key[target_url]
                records_index, status_msg, is_error, is_system_error = domain_outcomes[
                    domain_key
                ]
                if is_system_error:
                    for ref in references:
                        all_results.append(
                            {
                                "URL": target_url,
                                "File": file_type,
                                "Result": "System Error",
                                "Details": status_msg,
                                "Reference": str(ref["original"]),
                            }
                        )
                else:
                    all_results.extend(
                        _evaluate_references_for_target(
                            target_url,
                            file_type,
                            references,
                            records_index,
                            status_msg,
                            is_error,
                        )
                    )

            st.session_state.all_results_raw = all_results

    if st.session_state.all_results_raw is not None:
        st.markdown('<div class="compact-hr"></div>', unsafe_allow_html=True)
        st.subheader("Results")

        raw_data = st.session_state.all_results_raw
        if not raw_data:
            st.info("No results to display.")
        else:
            final_df = pd.DataFrame(raw_data)

            if view_mode == "Errors / Warnings Only":
                error_categories = _classify_errors_vectorized(final_df)
                final_df = final_df[error_categories.isin(error_filters)]

            if final_df.empty and view_mode == "Errors / Warnings Only":
                st.success("🎉 Great job! No errors found based on current filters.")
            elif final_df.empty:
                st.info("No results to display.")
            else:
                checked_file = st.session_state.get("checked_file_type", file_type)

                if layout_mode == "Horizontal (Aggregated)":
                    refs = st.session_state.processed_refs
                    lookup = {
                        (u, ref_orig): (res_val, det_val)
                        for u, res_val, det_val, ref_orig in zip(
                            final_df["URL"],
                            final_df["Result"],
                            final_df["Details"],
                            final_df["Reference"],
                        )
                    }

                    visible_urls = dict.fromkeys(final_df["URL"])
                    unique_targets = [
                        u for u in st.session_state.target_order if u in visible_urls
                    ]
                    if len(unique_targets) < len(visible_urls):
                        known_urls = set(unique_targets)
                        unique_targets.extend(
                            u for u in visible_urls if u not in known_urls
                        )

                    rows: list[dict[str, str]] = []
                    for url in unique_targets:
                        row_data: dict[str, str] = {
                            "URL": url,
                            "File": checked_file,
                        }
                        has_visible_data = False

                        for idx, ref in enumerate(refs, start=1):
                            ref_orig = str(ref["original"])
                            matched = lookup.get((url, ref_orig))
                            if matched is not None:
                                has_visible_data = True
                                row_data[f"Result {idx}"] = matched[0]
                                row_data[f"Details {idx}"] = matched[1]
                                row_data[f"Ref {idx}"] = ref_orig
                            else:
                                row_data[f"Result {idx}"] = "Filtered"
                                row_data[f"Details {idx}"] = "-"
                                row_data[f"Ref {idx}"] = ref_orig

                        if has_visible_data:
                            rows.append(row_data)

                    display_df = pd.DataFrame(rows)
                    result_cols = [
                        c for c in display_df.columns if c.startswith("Result")
                    ]
                    styled_df = _style_results_dataframe(display_df, result_cols)
                else:
                    display_df = final_df[
                        ["URL", "File", "Result", "Details", "Reference"]
                    ]
                    styled_df = _style_results_dataframe(display_df, ["Result"])

                st.dataframe(
                    styled_df,
                    height=600,
                    **_DATAFRAME_WIDTH_KWARGS,
                )

                csv_bytes = _prepare_safe_csv_bytes(display_df)
                st.download_button(
                    label=f"Download CSV ({view_mode})",
                    data=csv_bytes,
                    file_name="report.csv",
                    mime="text/csv",
                )


if _is_running_in_streamlit():
    render_app()
