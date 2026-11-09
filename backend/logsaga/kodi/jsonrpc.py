"""
Kodi JSON-RPC client — read-only, allowlist-enforced.

Layer 6: JSON-RPC allowlist. Only these method patterns are permitted:
  - JSONRPC.Ping
  - Files.PrepareDownload
  - Any method matching *.Get* (VideoLibrary.GetMovies, etc.)

Anything else (VideoLibrary.Set*, Remove*, Clean, Scan, Refresh, Player.*,
Input.*, System.*, Addons.*) raises KodiJSONRPCSecurityError before any
network request is sent.

Full artwork implementation is deferred to Phase 2. This stub enforces the
allowlist and provides a Ping method for the doctor command.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

import httpx

from logsaga.config import Settings


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class KodiJSONRPCError(Exception):
    """Base class for JSON-RPC errors."""


class KodiJSONRPCSecurityError(KodiJSONRPCError):
    """Raised when a method is not on the allowlist. Never catch and continue."""


# ---------------------------------------------------------------------------
# Layer 6: Allowlist
# ---------------------------------------------------------------------------
_ALLOWED_EXACT = frozenset({
    "JSONRPC.Ping",
    "Files.PrepareDownload",
})

# Any method whose name contains ".Get" (case-sensitive) is allowed.
# This covers VideoLibrary.GetMovies, VideoLibrary.GetEpisodes, etc.
_ALLOWED_GET_PATTERN = re.compile(r"\.Get", re.ASCII)


def classify_art_url(url: str) -> tuple[str, str]:
    """
    Classify an art URL from Kodi's art table.

    Kodi's art table stores raw URLs — no image:// wrapping. Categories:
      'http'  — starts with http:// or https:// → fetch directly
      'vfs'   — NFS/SMB/local path → needs Files.PrepareDownload
      'skip'  — plain text tag value, empty, or unrecognised → not an image

    Returns (kind, url) where url is unchanged.
    """
    if not url:
        return ("skip", url)

    # Defensively handle image:// wrapping (should not appear in DB, but just in case)
    if url.startswith("image://"):
        inner = url[8:]  # strip "image://"
        if inner.endswith("/"):
            inner = inner[:-1]
        inner = urllib.parse.unquote(inner)
        return classify_art_url(inner)

    if url.startswith("http://") or url.startswith("https://"):
        return ("http", url)

    if "://" in url:
        # NFS, SMB, FTP, etc. — Kodi VFS path, needs PrepareDownload
        return ("vfs", url)

    # Plain text (custom tag values from adjacent addons like service.customtagger)
    return ("skip", url)


def _wrap_image_url(url: str) -> str:
    """
    Wrap a raw URL in Kodi's image:// scheme for use with Files.PrepareDownload.
    e.g. 'nfs://host/path/file.jpg' → 'image://nfs%3a%2f%2fhost%2fpath%2ffile.jpg/'
    """
    encoded = urllib.parse.quote(url, safe="")
    return f"image://{encoded}/"


def _check_method(method: str) -> None:
    """
    Layer 6: Raise KodiJSONRPCSecurityError if the method is not on the allowlist.
    Called before every request.
    """
    if method in _ALLOWED_EXACT:
        return
    if _ALLOWED_GET_PATTERN.search(method):
        return
    raise KodiJSONRPCSecurityError(
        f"JSON-RPC method '{method}' is not on the allowlist. "
        f"Permitted: exact matches {sorted(_ALLOWED_EXACT)}, "
        f"and any method containing '.Get'. "
        f"See CLAUDE.md 'Kodi database protection', Layer 6."
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class KodiJSONRPC:
    """
    Minimal JSON-RPC client for Kodi.

    Every call goes through the allowlist check before any network I/O.
    """

    def __init__(self, cfg: Settings) -> None:
        self._url = cfg.kodi_jsonrpc_url
        auth = None
        if cfg.kodi_jsonrpc_user:
            auth = (cfg.kodi_jsonrpc_user, cfg.kodi_jsonrpc_password)
        self._client = httpx.Client(
            auth=auth,
            timeout=10.0,
            headers={"Content-Type": "application/json"},
        )
        self._request_id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """
        Make a synchronous JSON-RPC call.

        Raises KodiJSONRPCSecurityError if the method is not allowed.
        Raises KodiJSONRPCError on network or protocol errors.
        Returns the 'result' field from the response.
        """
        _check_method(method)

        self._request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._request_id,
        }
        if params:
            payload["params"] = params

        try:
            resp = self._client.post(self._url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise KodiJSONRPCError(f"HTTP error calling {method}: {exc}") from exc

        data = resp.json()
        if "error" in data:
            raise KodiJSONRPCError(
                f"JSON-RPC error from {method}: {data['error']}"
            )
        return data.get("result")

    def ping(self) -> bool:
        """Return True if Kodi responds to a ping."""
        try:
            result = self.call("JSONRPC.Ping")
            return result == "pong"
        except KodiJSONRPCError:
            return False

    def prepare_download(self, path: str) -> str:
        """
        Call Files.PrepareDownload and return an HTTP URL to download a Kodi VFS file.

        path: a Kodi VFS path, either raw ('nfs://...') or already wrapped in
              image:// ('image://nfs%3a%2f%2f.../').  Raw paths are wrapped
              automatically.

        Returns the HTTP download URL (e.g. http://kodi:8086/vfs/nfs%3a%2f%2f...).
        Raises KodiJSONRPCError if Kodi cannot prepare the path.
        """
        # Kodi expects the image:// wrapped form for PrepareDownload
        if not path.startswith("image://"):
            path = _wrap_image_url(path)

        result = self.call("Files.PrepareDownload", {"path": path})

        if not result:
            raise KodiJSONRPCError(
                f"Files.PrepareDownload returned empty result for {path!r}"
            )

        protocol = result.get("protocol")
        if protocol == "http":
            details = result.get("details", {})
            file_path = details.get("path", "")
            # Construct the download URL from the JSON-RPC base URL.
            # Kodi returns path without a leading '/' (e.g. "image/image%3a%2f%2f...")
            # so we must add one to avoid running the path into the port number.
            base_url = self._url.rsplit("/jsonrpc", 1)[0]
            if file_path and not file_path.startswith("/"):
                file_path = "/" + file_path
            return f"{base_url}{file_path}"

        raise KodiJSONRPCError(
            f"Files.PrepareDownload returned unexpected protocol {protocol!r} "
            f"for {path!r}. Full result: {result}"
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KodiJSONRPC":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
