"""Read-only client for the NestJS API. Every call carries the asking user's own JWT, so the
backend's authorization (org membership, hidden-test stripping, own-submissions-only) applies to the AI too."""
import os

import httpx

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:3000")


class Unauthorized(Exception):
    pass


class NotAccessible(Exception):
    pass


class Backend:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(base_url=BACKEND_URL, timeout=10)
        self._owns_client = client is None

    async def get(self, path: str):
        # Only GET is ever issued: the AI layer is read-only by construction.
        r = await self._client.get(path, headers=self._headers)
        if r.status_code == 401:
            raise Unauthorized()
        if r.status_code in (403, 404):
            raise NotAccessible(path)
        r.raise_for_status()
        return r.json()

    async def close(self):
        if self._owns_client:
            await self._client.aclose()
