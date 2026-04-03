from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional
from urllib.parse import urlparse


SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks5"}


@dataclass(frozen=True)
class ProxyConfig:
    raw: str
    scheme: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None

    @property
    def display_url(self) -> str:
        auth = ""
        if self.username:
            auth = self.username
            if self.password:
                auth += ":***"
            auth += "@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}"


def parse_proxy(proxy: Optional[str]) -> Optional[ProxyConfig]:
    if proxy is None:
        return None

    value = proxy.strip()
    if not value:
        return None

    parsed = urlparse(value)
    scheme = (parsed.scheme or "").lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        raise ValueError("Proxy protocol must be one of: http, https, socks5")

    if not parsed.hostname:
        raise ValueError("Proxy host is required")

    if parsed.port is None:
        raise ValueError("Proxy port is required")

    return ProxyConfig(
        raw=value,
        scheme=scheme,
        host=parsed.hostname,
        port=parsed.port,
        username=parsed.username,
        password=parsed.password,
    )


def _proxy_env_key_values(proxy_url: str) -> dict[str, str]:
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "ALL_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "all_proxy": proxy_url,
    }


@contextmanager
def use_proxy(proxy: Optional[ProxyConfig]) -> Iterator[Optional[ProxyConfig]]:
    if proxy is None:
        yield None
        return

    env_updates = _proxy_env_key_values(proxy.raw)
    previous_env: dict[str, Optional[str]] = {key: os.environ.get(key) for key in env_updates}

    original_socket = socket.socket
    using_socks_patch = False

    try:
        for key, value in env_updates.items():
            os.environ[key] = value

        if proxy.scheme == "socks5":
            try:
                import socks  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("socks5 proxy requires PySocks package") from exc

            socks.set_default_proxy(
                socks.SOCKS5,
                proxy.host,
                proxy.port,
                username=proxy.username,
                password=proxy.password,
            )
            socket.socket = socks.socksocket
            using_socks_patch = True

        yield proxy
    finally:
        if using_socks_patch:
            try:
                import socks  # type: ignore[import-not-found]

                socks.set_default_proxy()
            except Exception:
                pass
            socket.socket = original_socket

        for key, old_value in previous_env.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
