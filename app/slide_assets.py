"""Only authenticated slide images are public assets; metadata stays internal."""
from pathlib import Path
import secrets

from fastapi import HTTPException, Request
from fastapi.staticfiles import StaticFiles

from app.config import settings


class SlideAssets(StaticFiles):
    async def get_response(self, path: str, scope):
        # Never expose index.json, embeddings, backups or arbitrary files,
        # including to an authenticated display. It only needs raster images.
        if Path(path).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
            raise HTTPException(status_code=404)
        token = Request(scope).query_params.get("token", "")
        if settings.ws_token and not secrets.compare_digest(
                token.encode("utf-8"), settings.ws_token.encode("utf-8")):
            raise HTTPException(status_code=403)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "private, no-store"
        return response
