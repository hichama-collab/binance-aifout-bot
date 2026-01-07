import time
import hmac
import hashlib
from urllib.parse import urlencode

import requests
from requests.exceptions import ReadTimeout, ConnectionError, HTTPError

class Binance:
    def __init__(self, apiKey: str, apiSecret: str, baseUrl: str, httpTimeout: int, httpRetries: int, httpBackoff: float):
        self.baseUrl = baseUrl
        self.httpTimeout = httpTimeout
        self.httpRetries = httpRetries
        self.httpBackoff = httpBackoff

        self.apiSecret = apiSecret.encode()
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": apiKey})

        self.timeOffset = 0
        self.syncTime()

    def syncTime(self) -> None:
        r = requests.get(f"{self.baseUrl}/api/v3/time", timeout=self.httpTimeout)
        r.raise_for_status()
        self.timeOffset = r.json()["serverTime"] - int(time.time() * 1000)

    def sign(self, params: dict) -> str:
        q = urlencode(params)
        sig = hmac.new(self.apiSecret, q.encode(), hashlib.sha256).hexdigest()
        return q + "&signature=" + sig

    def backoff(self, attempt: int) -> None:
        time.sleep(self.httpBackoff * (2 ** (attempt - 1)))

    def get(self, path: str, params=None, signed: bool=False):
        if params is None:
            params = {}

        for attempt in range(1, self.httpRetries + 1):
            try:
                if signed:
                    params["timestamp"] = int(time.time() * 1000) + self.timeOffset
                    params["recvWindow"] = 5000
                    q = self.sign(params)
                else:
                    q = urlencode(params)

                url = f"{self.baseUrl}{path}?{q}"
                r = self.session.get(url, timeout=self.httpTimeout)
                r.raise_for_status()
                return r.json()

            except HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                if code == 401:
                    try:
                        self.syncTime()
                    except Exception:
                        pass
                    if attempt < self.httpRetries:
                        self.backoff(attempt)
                        continue
                    raise

                if code in (429, 500, 502, 503, 504) and attempt < self.httpRetries:
                    self.backoff(attempt)
                    continue
                raise

            except (ReadTimeout, ConnectionError):
                if attempt == self.httpRetries:
                    raise
                self.backoff(attempt)

    def post(self, path: str, params: dict):
        for attempt in range(1, self.httpRetries + 1):
            try:
                params["timestamp"] = int(time.time() * 1000) + self.timeOffset
                params["recvWindow"] = 5000
                q = self.sign(params)
                r = self.session.post(f"{self.baseUrl}{path}", data=q, timeout=self.httpTimeout)
                r.raise_for_status()
                return r.json()

            except HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                if code == 401:
                    try:
                        self.syncTime()
                    except Exception:
                        pass
                    if attempt < self.httpRetries:
                        self.backoff(attempt)
                        continue
                    raise

                if code in (429, 500, 502, 503, 504) and attempt < self.httpRetries:
                    self.backoff(attempt)
                    continue
                raise

            except (ReadTimeout, ConnectionError):
                if attempt == self.httpRetries:
                    raise
                self.backoff(attempt)

    def delete(self, path: str, params: dict):
        for attempt in range(1, self.httpRetries + 1):
            try:
                params["timestamp"] = int(time.time() * 1000) + self.timeOffset
                params["recvWindow"] = 5000
                q = self.sign(params)
                r = self.session.delete(f"{self.baseUrl}{path}", data=q, timeout=self.httpTimeout)
                r.raise_for_status()
                return r.json()

            except HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                if code == 401:
                    try:
                        self.syncTime()
                    except Exception:
                        pass
                    if attempt < self.httpRetries:
                        self.backoff(attempt)
                        continue
                    raise

                if code in (429, 500, 502, 503, 504) and attempt < self.httpRetries:
                    self.backoff(attempt)
                    continue
                raise

            except (ReadTimeout, ConnectionError):
                if attempt == self.httpRetries:
                    raise
                self.backoff(attempt)
