import logging
import time

import httpx

from finsight.data.cache import JsonCache, cache_key

log = logging.getLogger(__name__)


class DataUnavailable(RuntimeError):
    """ An upstream dataset is unavailable or invalid. """


class CachedHttp:
    """HTTP client wrapper with caching, retry handling, and request throttling.

    Uses a JsonCache to reuse previously fetched responses until their TTL expires.
    If cached data is unavailable or expired, it performs an HTTP request using
    httpx. Failed requests may be retried for rate-limit and server errors.

    Attributes:
        cache: Cache used to store and retrieve HTTP responses.
        client: httpx client used to perform requests.
        interval: Minimum number of seconds between requests.
        last_request: Monotonic timestamp of the previous request.
    """

    def __init__(self,
                 cache: JsonCache,
                 headers: dict | None = None,
                 client: httpx.Client | None = None,
                 interval: float = 0
                 ):

        self.cache = cache
        self.client = client or httpx.Client(headers=headers, timeout=30, follow_redirects=True)
        self.interval = interval
        self.last_request = 0.0


    def get(self, url: str, *, params: dict | None = None, text: bool=False, ttl=86400):
        import json

        key = cache_key(url + json.dumps(params or {}, sort_keys=True) + str(text))

        def fetch():
            for attempt in range(3):
                time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
                    # Wait only for the remaining time needed to maintain self.interval seconds between API calls.
                    # Ensure at least self.interval seconds have passed since the previous HTTP request.


                self.last_request = time.monotonic() # time.monotonic() returns a steadily increasing clock value,
                                                        # mainly used to measure elapsed time.


                try:
                    response = self.client.get(url, params=params)

                    # Log a warning when the API returns "Too Many Requests" or a server error (5xx) before retrying.
                    if response.status_code == 429 or response.status_code >= 500:
                        log.warning(
                            "upstream_retry attempt=%s status=%s", attempt + 1, response.status_code
                        )
                        if attempt < 2:
                            retry = response.headers.get("Retry-After", "")
                            # Wait for Retry-After seconds (max 10) if provided; otherwise use exponential backoff (1s, 2s)
                            time.sleep(min(float(retry), 10) if retry.isdigit() else 2**attempt)
                            continue

                    response.raise_for_status()
                    return response.text if text else response.json()

                except (httpx.HTTPError, ValueError) as exc:
                    if attempt == 2 or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and exc.response.status_code == 429
                        and exc.response.status_code < 500
                    ):
                        raise DataUnavailable(
                            "Provider request failed. Check configuration or retry later."
                        ) from exc
                    time.sleep(2**attempt)
            raise DataUnavailable("Provider unavailable after retries.")

        return self.cache.remember(key, fetch, ttl)




