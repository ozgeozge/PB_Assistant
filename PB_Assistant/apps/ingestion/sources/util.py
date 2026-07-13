import json
import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterable, Generator, Annotated, TypeVar
from pathlib import Path
from time import perf_counter, sleep
from typing_extensions import override
from httpx import Client, URL, USE_CLIENT_DEFAULT, Response, codes, HTTPError
from httpx._client import UseClientDefault
from httpx._types import (
    RequestContent,
    RequestData,
    RequestFiles,
    QueryParamTypes,
    HeaderTypes,
    CookieTypes,
    AuthTypes,
    TimeoutTypes,
    RequestExtensions,
)
T = TypeVar('T')
D = TypeVar('D')

def get_value(val: Callable[[], T], default: D | None = None) -> T | D:
    try:
        ret = val()
        if ret is None:
            return default
        return ret
    except (KeyError, AttributeError):
        return default

def get(obj: dict[str, Any], *keys: str, default: Any = None) -> Any:  # type: ignore[var-annotated]
    for key in keys:
        obj = obj.get(key)  # type: ignore[assignment]
        if obj is None:
            return default  # type: ignore[unreachable]
    return obj

def response_logger(logger: logging.Logger) -> Callable[[Response], dict[str, Any]]:
    def inner(response: Response) -> dict[str, Any]:
        # nonlocal logger
        logger.warning(response.text)
        return {}

    return inner

class RequestClient(Client):
    def __init__(self,  # type: ignore[no-untyped-def]
                 *,
                 max_req_per_sec: int = 5,
                 max_retries: int = 5,
                 backoff_rate: float = 120.,
                 retry_on_status: list[int] | None = None,
                 **kwargs) -> None:
        super().__init__(**kwargs)

        self.max_req_per_sec = max_req_per_sec
        self.time_per_request = 1 / max_req_per_sec
        self.max_retries = max_retries
        self.backoff_rate = backoff_rate
        self.last_request: float | None = None
        self.retry_on_status = retry_on_status or [
            codes.INTERNAL_SERVER_ERROR,  # 500
            codes.BAD_GATEWAY,  # 502
            codes.SERVICE_UNAVAILABLE,  # 503
            codes.GATEWAY_TIMEOUT,  # 504
        ]
        self.kwargs = kwargs
        self.callbacks: dict[int, Callable[..., Any]] = {}

    def on(self, status: int, func: Callable[[Response], dict[str, Any]]) -> None:
        self.callbacks[status] = func

    @override
    def request(
            self,
            method: str,
            url: URL | str,
            *,
            content: RequestContent | None = None,
            data: RequestData | None = None,
            files: RequestFiles | None = None,
            json: Any | None = None,
            params: QueryParamTypes | None = None,
            headers: HeaderTypes | None = None,
            cookies: CookieTypes | None = None,
            auth: AuthTypes | UseClientDefault | None = None,
            follow_redirects: bool | UseClientDefault = True,
            timeout: TimeoutTypes | UseClientDefault = 120,
            extensions: RequestExtensions | None = None,
    ) -> Response:
        for retry in range(self.max_retries):
            # Check if we need to wait before the next request so we are staying below the rate limit
            time = perf_counter() - (self.last_request or 0)
            if time < self.time_per_request:
                logging.debug(f'Sleeping to keep rate limit: {self.time_per_request - time:.4f} seconds')
                sleep(self.time_per_request - time)

            if auth == USE_CLIENT_DEFAULT:
                auth = None
            if follow_redirects == USE_CLIENT_DEFAULT:
                follow_redirects = None
            if timeout == USE_CLIENT_DEFAULT:
                timeout = None

            # Log latest request
            self.last_request = perf_counter()
            response = super().request(
                method=method or self.kwargs.get('method'),
                url=url or self.kwargs.get('url'),
                content=content or self.kwargs.get('content'),
                data=data or self.kwargs.get('data'),
                files=files or self.kwargs.get('files'),
                json=json or self.kwargs.get('json'),
                params=params or self.kwargs.get('params'),
                headers=self.kwargs.get('headers', {}) | (headers or {}),
                cookies=self.kwargs.get('cookies', {}) | (cookies or {}),
                auth=auth or self.kwargs.get('auth', USE_CLIENT_DEFAULT),
                follow_redirects=follow_redirects or self.kwargs.get('follow_redirects', True),
                timeout=timeout or self.kwargs.get('timeout', 120),
                extensions=extensions or self.kwargs.get('extensions'),
            )

            try:
                response.raise_for_status()

                # reset counters after successful request
                self.time_per_request = 1 / self.max_req_per_sec

                return response

            except HTTPError as e:
                if e.response.status_code in self.callbacks:  # type: ignore[attr-defined]
                    logging.debug(f'Found status handler for {e.response.status_code}')  # type: ignore[attr-defined]
                    update = self.callbacks[e.response.status_code](e.response)  # type: ignore[attr-defined]
                    if update and update.get('content'):
                        content = update.get('content')
                    if update and update.get('data'):
                        data = update.get('data')
                    if update and update.get('json'):
                        if not json:
                            json = update.get('json', None)
                        else:
                            json.update(update.get('json', {}))
                    if update and update.get('params'):
                        if not params:
                            params = update.get('params', None)
                        else:
                            params.update(update.get('params', {}))  # type: ignore[union-attr]
                    if update and update.get('headers'):
                        if not headers:
                            headers = update.get('headers', None)
                        else:
                            headers.update(update.get('headers', {}))  # type: ignore[union-attr]

                # if this error is not on the list, pass on error right away; otherwise log and retry
                elif e.response.status_code not in self.retry_on_status and len(self.retry_on_status) > 0:  # type: ignore[attr-defined]
                    logging.warning(e.response.text)  # type: ignore[attr-defined]
                    raise e

                else:
                    logging.warning(f'Retry {retry} after failing to retrieve from {url}: {e}')
                    logging.warning(e.response.text)  # type: ignore[attr-defined]
                    logging.exception(e)

                    # grow the sleep time between requests
                    self.time_per_request = (self.time_per_request + 1) * self.backoff_rate
        else:
            raise RuntimeError('Maximum number of retries reached')

def clear_empty(obj: Any | None) -> Any | None:
    """
    Recursively checks the object for empty-like things and explicitly sets them to None (or drops keys)

    :param obj:
    :return:
    """
    if obj is None:
        return None

    if isinstance(obj, str):
        if len(obj) == 0:
            return None
        return obj

    if isinstance(obj, list):
        tmp_l = [clear_empty(li) for li in obj]
        tmp_l = [li for li in tmp_l if li is not None]
        if len(tmp_l) > 0:
            return tmp_l
        return None

    if isinstance(obj, dict):
        tmp_d = {key: clear_empty(val) for key, val in obj.items()}
        tmp_d = {key: val for key, val in tmp_d.items() if val is not None}
        if len(tmp_d) > 0:
            return tmp_d
        return None

    return obj


def as_uuid(val: str | uuid.UUID | None = None) -> uuid.UUID | None:
    if val is None:
        return None
    if type(val) == str:
        return uuid.UUID(val)
    return val  # type: ignore[return-value]