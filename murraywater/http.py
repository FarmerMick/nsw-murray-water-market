"""Polite HTTP client: honest User-Agent, a minimum gap per host, bounded retries."""

import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger(__name__)


class FetchError(Exception):
    pass


class PoliteClient:
    def __init__(self, user_agent=config.USER_AGENT, min_gap=config.MIN_GAP_SECONDS,
                 timeout=config.HTTP_TIMEOUT, retries=config.HTTP_RETRIES, sleep=time.sleep):
        self.user_agent = user_agent
        self.min_gap = min_gap
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep
        self.requests_made = 0
        self._last = {}

    def _wait_turn(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = time.monotonic() - last
            if gap < self.min_gap:
                self.sleep(self.min_gap - gap)
        self._last[host] = time.monotonic()

    def fetch(self, url: str, form: dict | None = None) -> bytes:
        """GET, or POST when `form` is given. Retries network errors, 429 and 5xx."""
        host = urllib.parse.urlsplit(url).netloc
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        for attempt in range(1, self.retries + 1):
            self._wait_turn(host)
            request = urllib.request.Request(url, data=data, headers={"User-Agent": self.user_agent})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self.requests_made += 1
                    return response.read()
            except urllib.error.HTTPError as exc:
                self.requests_made += 1
                if exc.code != 429 and exc.code < 500:
                    raise FetchError(f"HTTP {exc.code} from {url}") from exc
                reason = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                reason = str(exc)
            if attempt == self.retries:
                raise FetchError(f"{reason} from {url} after {attempt} attempts")
            backoff = 2 ** attempt
            log.warning("%s from %s; retrying in %ss", reason, host, backoff)
            self.sleep(backoff)
        raise FetchError(f"unreachable: {url}")
