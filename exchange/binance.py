import time
import hmac
import hashlib
from urllib.parse import urlencode

import requests
from requests.exceptions import ReadTimeout, ConnectionError, HTTPError


class BinanceApiError(RuntimeError):
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg
        super().__init__(f"BinanceApiError code={code} msg={msg}")


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

    def _http_error_message(self, e: HTTPError, method: str, path: str) -> str:
        response = getattr(e, "response", None)
        code = response.status_code if response is not None else 0
        body = ""
        if response is not None:
            try:
                body = response.text.strip()
            except Exception:
                body = ""
        return f"{method} {path} HTTP {code} body={body}"

    def _raise_api_error(self, e: HTTPError, method: str, path: str):
        response = getattr(e, "response", None)
        if response is None:
            raise RuntimeError(self._http_error_message(e, method, path)) from e
        try:
            data = response.json()
        except Exception:
            data = {}
        if isinstance(data, dict) and "code" in data:
            raise BinanceApiError(data.get("code"), data.get("msg", "")) from e
        raise RuntimeError(self._http_error_message(e, method, path)) from e

    @staticmethod
    def _retryable_api_code(code) -> bool:
        try:
            return int(code) in {-1003, -1021}
        except Exception:
            return False

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
                api_code = None
                try:
                    api_code = e.response.json().get("code") if e.response is not None else None
                except Exception:
                    api_code = None
                if code == 401 or api_code == -1021:
                    try:
                        self.syncTime()
                    except Exception:
                        pass
                    if attempt < self.httpRetries:
                        self.backoff(attempt)
                        continue
                    self._raise_api_error(e, "GET", path)

                if (code in (429, 500, 502, 503, 504) or self._retryable_api_code(api_code)) and attempt < self.httpRetries:
                    self.backoff(attempt)
                    continue
                self._raise_api_error(e, "GET", path)

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
                api_code = None
                try:
                    api_code = e.response.json().get("code") if e.response is not None else None
                except Exception:
                    api_code = None
                if code == 401 or api_code == -1021:
                    try:
                        self.syncTime()
                    except Exception:
                        pass
                    if attempt < self.httpRetries:
                        self.backoff(attempt)
                        continue
                    self._raise_api_error(e, "POST", path)

                if (code in (429, 500, 502, 503, 504) or self._retryable_api_code(api_code)) and attempt < self.httpRetries:
                    self.backoff(attempt)
                    continue
                self._raise_api_error(e, "POST", path)

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
                api_code = None
                try:
                    api_code = e.response.json().get("code") if e.response is not None else None
                except Exception:
                    api_code = None
                if code == 401 or api_code == -1021:
                    try:
                        self.syncTime()
                    except Exception:
                        pass
                    if attempt < self.httpRetries:
                        self.backoff(attempt)
                        continue
                    self._raise_api_error(e, "DELETE", path)

                if (code in (429, 500, 502, 503, 504) or self._retryable_api_code(api_code)) and attempt < self.httpRetries:
                    self.backoff(attempt)
                    continue
                self._raise_api_error(e, "DELETE", path)

            except (ReadTimeout, ConnectionError):
                if attempt == self.httpRetries:
                    raise
                self.backoff(attempt)
