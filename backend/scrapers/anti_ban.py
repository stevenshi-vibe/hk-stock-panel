"""
Anti-ban HTTP layer for scrapers.
Provides:
- Rotating User-Agent pool
- Global + per-host rate limiting (token-bucket style)
- Exponential backoff with jitter on failure / 429 / 5xx
- Auto-retry on transient errors (connection reset, timeout, 429, 502/503/504)
- Session rotation (new session = new cookie jar) after N failures
- Optional proxy list support (HTTP(S) proxies, round-robin)
- Thread-safe: uses a lock per host for rate limiting
"""
import time
import random
import threading
import logging
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ============================================================
# User-Agent pool (desktop Chrome / Edge / Firefox / Safari, recent versions)
# ============================================================
USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
    # Firefox Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:130.0) Gecko/20100101 Firefox/130.0",
    # Safari Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

# Accept-Language rotations
ACCEPT_LANGS = [
    "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
    "zh-HK,zh;q=0.9,en;q=0.8,zh-TW;q=0.7",
    "en-US,en;q=0.9,zh-CN;q=0.8",
    "zh-TW,zh;q=0.9,en;q=0.8",
    "en-GB,en;q=0.9,zh;q=0.7",
]

# Common Accept headers
ACCEPTS = [
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "application/json, text/javascript, */*; q=0.01",
]


def _random_headers(overrides: dict = None) -> dict:
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": random.choice(ACCEPT_LANGS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if overrides:
        h.update(overrides)
    return h


# ============================================================
# Per-host rate limiter (simple token bucket / minimum interval)
# ============================================================
class RateLimiter:
    """Enforce a minimum interval between requests to the same host."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_call: dict[str, float] = {}

    def wait(self, host: str, min_interval: float):
        with self._lock:
            now = time.time()
            last = self._last_call.get(host, 0)
            wait_time = min_interval - (now - last)
            if wait_time > 0:
                # Release lock while sleeping so other hosts aren't blocked
                self._lock.release()
                try:
                    time.sleep(wait_time)
                finally:
                    self._lock.acquire()
            self._last_call[host] = time.time()


_global_limiter = RateLimiter()


# ============================================================
# Proxy support
# ============================================================
_proxies: list[str] = []
_proxy_idx = 0
_proxy_lock = threading.Lock()


def configure_proxies(proxy_list: list[str]):
    """Set a list of HTTP/HTTPS proxies. Round-robin across new sessions."""
    global _proxies
    _proxies = list(proxy_list or [])
    logger.info(f"[AntiBan] Configured {len(_proxies)} proxies")


def _next_proxy() -> Optional[dict]:
    global _proxy_idx
    if not _proxies:
        return None
    with _proxy_lock:
        p = _proxies[_proxy_idx % len(_proxies)]
        _proxy_idx += 1
    return {"http": p, "https": p}


# ============================================================
# Session factory
# ============================================================
def _build_session(referer: str = None, extra_headers: dict = None) -> requests.Session:
    s = requests.Session()
    headers = _random_headers(extra_headers)
    if referer:
        headers["Referer"] = referer
    s.headers.update(headers)

    # Retry adapter for low-level connection errors (DNS reset, connection refused)
    retry = Retry(
        total=0,  # We handle retries ourselves for HTTP-level control
        connect=2,
        backoff_factor=0.5,
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
    s.mount("https://", adapter)
    s.mount("http://", adapter)

    proxy = _next_proxy()
    if proxy:
        s.proxies.update(proxy)

    return s


# ============================================================
# Smart request function with retry + ban detection + backoff
# ============================================================
class AntiBanClient:
    """
    Thread-safe HTTP client with anti-ban measures.
    Each thread gets its own session; sessions are rotated after failures.
    """

    # How many consecutive failures before rotating session
    ROTATE_AFTER_FAILURES = 3
    # Status codes that signal a ban / throttle
    BAN_STATUSES = {403, 429, 503, 509, 418}
    TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
    # How many "soft" failures per host before we increase the global delay
    CIRCUIT_THRESHOLD = 10
    CIRCUIT_COOLDOWN = 60  # seconds

    def __init__(self, min_interval: float = 1.0, max_retries: int = 5,
                 timeout: int = 30, referer: str = None, extra_headers: dict = None):
        self._min_interval = min_interval
        self._base_interval = min_interval
        self._max_retries = max_retries
        self._timeout = timeout
        self._referer = referer
        self._extra_headers = extra_headers or {}
        self._tl = threading.local()
        self._failure_lock = threading.Lock()
        self._host_failures: dict[str, int] = {}
        self._circuit_open_until: dict[str, float] = {}

    def _get_session(self, rotate: bool = False) -> requests.Session:
        s = getattr(self._tl, "session", None)
        if s is None or rotate:
            s = _build_session(self._referer, self._extra_headers)
            self._tl.session = s
            self._tl.failures = 0
        return s

    def _current_interval(self, host: str) -> float:
        """Increase interval when we see repeated failures for a host."""
        with self._failure_lock:
            fails = self._host_failures.get(host, 0)
        if fails == 0:
            return self._base_interval
        # Exponentially increase: 1x, 2x, 4x, 8x, ..., capped at 15s
        multiplier = min(2 ** min(fails // 3, 4), 16)
        return min(self._base_interval * multiplier, 15.0)

    def _record_failure(self, host: str):
        with self._failure_lock:
            self._host_failures[host] = self._host_failures.get(host, 0) + 1
            fails = self._host_failures[host]
            if fails >= self.CIRCUIT_THRESHOLD:
                self._circuit_open_until[host] = time.time() + self.CIRCUIT_COOLDOWN
                logger.warning(
                    f"[AntiBan] Circuit breaker OPEN for {host} "
                    f"after {fails} failures, cooldown {self.CIRCUIT_COOLDOWN}s"
                )

    def _record_success(self, host: str):
        with self._failure_lock:
            self._host_failures[host] = 0
            self._circuit_open_until.pop(host, None)

    def _check_circuit(self, host: str):
        with self._failure_lock:
            until = self._circuit_open_until.get(host, 0)
        if until > time.time():
            wait = until - time.time()
            logger.warning(f"[AntiBan] Circuit breaker active for {host}, sleeping {wait:.1f}s")
            time.sleep(wait)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make an HTTP request with anti-ban protections."""
        from urllib.parse import urlparse
        host = urlparse(url).netloc

        self._check_circuit(host)

        last_exc = None
        session = self._get_session()
        consecutive_failures = getattr(self._tl, "failures", 0)

        for attempt in range(self._max_retries):
            interval = self._current_interval(host)
            # Add jitter: ±30%
            actual_interval = interval * (0.7 + random.random() * 0.6)
            _global_limiter.wait(host, actual_interval if attempt == 0 else actual_interval * 2)

            try:
                resp = session.request(
                    method, url,
                    timeout=kwargs.pop("timeout", self._timeout),
                    **kwargs
                )

                if resp.status_code == 200:
                    self._record_success(host)
                    self._tl.failures = 0
                    return resp

                # Handle ban / throttle
                if resp.status_code in self.BAN_STATUSES or resp.status_code in self.TRANSIENT_STATUSES:
                    self._record_failure(host)
                    consecutive_failures += 1
                    self._tl.failures = consecutive_failures

                    # Check Retry-After header
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait_s = float(retry_after) + random.uniform(0.5, 2.0)
                        except ValueError:
                            wait_s = 5
                    else:
                        # Exponential backoff with full jitter
                        wait_s = min(2 ** attempt, 60) * (0.5 + random.random())

                    logger.warning(
                        f"[AntiBan] {method} {host} -> {resp.status_code} "
                        f"(attempt {attempt+1}/{self._max_retries}), "
                        f"backoff {wait_s:.1f}s"
                    )

                    # Rotate session after too many failures (new UA + new cookies)
                    if consecutive_failures >= self.ROTATE_AFTER_FAILURES:
                        logger.info(f"[AntiBan] Rotating session for {host} after {consecutive_failures} failures")
                        session = self._get_session(rotate=True)
                        consecutive_failures = 0

                    time.sleep(wait_s)
                    continue

                # Non-retryable error (404, 400, etc.)
                resp.raise_for_status()
                return resp

            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                self._record_failure(host)
                consecutive_failures += 1
                self._tl.failures = consecutive_failures
                last_exc = e
                wait_s = min(2 ** attempt, 30) * (0.5 + random.random())
                logger.warning(
                    f"[AntiBan] {method} {host} connection error: {e} "
                    f"(attempt {attempt+1}/{self._max_retries}), backoff {wait_s:.1f}s"
                )
                if consecutive_failures >= self.ROTATE_AFTER_FAILURES:
                    session = self._get_session(rotate=True)
                    consecutive_failures = 0
                time.sleep(wait_s)
                continue

        # All retries exhausted
        raise last_exc or requests.exceptions.HTTPError(
            f"Failed after {self._max_retries} retries for {url}"
        )

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)


# ============================================================
# Pre-configured clients for our upstream sources
# ============================================================

def make_ccass_client() -> AntiBanClient:
    """Client for HKEX CCASS search (hkexnews.hk). Conservative rate.
    Note: POST-specific headers (Origin, Content-Type, Referer) are added
    by callers on POST requests; the base client uses browser-like defaults.
    """
    return AntiBanClient(
        min_interval=1.5,   # HKEX is sensitive to CCASS scraping
        max_retries=5,
        timeout=30,
        extra_headers={
            "Referer": "https://www3.hkexnews.hk/sdw/search/searchsdw_c.aspx",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        }
    )


def make_hkex_news_client() -> AntiBanClient:
    """Client for HKEX news/announcements (hkexnews.hk titleSearch)."""
    return AntiBanClient(
        min_interval=1.0,
        max_retries=4,
        timeout=25,
        extra_headers={
            "Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh",
        }
    )


def make_tencent_client() -> AntiBanClient:
    """Client for Tencent Finance (qt.gtimg.cn / proxy.finance.qq.com)."""
    return AntiBanClient(
        min_interval=0.3,   # Tencent is tolerant
        max_retries=3,
        timeout=15,
        extra_headers={"Referer": "https://finance.qq.com/"},
    )


def make_yahoo_client() -> AntiBanClient:
    """Client for Yahoo Finance."""
    return AntiBanClient(
        min_interval=0.5,
        max_retries=4,
        timeout=15,
    )


def make_sina_client() -> AntiBanClient:
    """Client for Sina Finance."""
    return AntiBanClient(
        min_interval=0.3,
        max_retries=3,
        timeout=10,
        extra_headers={"Referer": "https://finance.sina.com.cn/"},
    )


def make_eastmoney_client() -> AntiBanClient:
    """东方财富数据中心。当前接口没有商用授权，只作为可替换 provider 的一种实现。"""
    return AntiBanClient(
        min_interval=0.6,
        max_retries=4,
        timeout=25,
        extra_headers={"Referer": "https://data.eastmoney.com/hsgt/"},
    )


def make_eastmoney_fund_client() -> AntiBanClient:
    """天天基金持仓。Referer 由调用方按基金代码覆盖，缺了会 404。"""
    return AntiBanClient(
        min_interval=0.7,
        max_retries=4,
        timeout=25,
        extra_headers={
            "Referer": "https://fundf10.eastmoney.com/",
            "X-Requested-With": "XMLHttpRequest",
        },
    )


def make_hkex_di_client() -> AntiBanClient:
    """港交所权益披露日报。再分发前仍应核对站点条款。"""
    return AntiBanClient(
        min_interval=1.2,
        max_retries=4,
        timeout=30,
        extra_headers={"Referer": "https://di.hkex.com.hk/"},
    )


def make_hkex_mutualmarket_client() -> AntiBanClient:
    """港股通持股页与 CCASS 查询同一路径，条款限制个人非商业使用。默认只请求最近一个持股日。"""
    return AntiBanClient(
        min_interval=1.5,
        max_retries=3,
        timeout=30,
        extra_headers={
            "Referer": "https://www3.hkexnews.hk/sdw/search/mutualmarket.aspx?t=hk",
        },
    )
