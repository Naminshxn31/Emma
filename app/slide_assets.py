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
        if (Path(path).name != path or Path(path).suffix.lower()
                not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}):
            raise HTTPException(status_code=404)
        token = Request(scope).query_params.get("token", "")
        if settings.ws_token and not secrets.compare_digest(
                token.encode("utf-8"), settings.ws_token.encode("utf-8")):
            raise HTTPException(status_code=403)
        # A guessed URL is still customer-facing egress. StaticFiles alone
        # would serve any image dropped beside the deck, including an old or
        # wrong-project export absent from the registered asset manifest.
        from app.tools import slides

        source = next((item for item in slides.load_slides()
                       if item.get("file") == path), None)
        if (source is None or slides.display_payload({
                "id": source.get("id"), "source_id": "slide_catalog",
                "project_id": settings.project_id}) is None):
            raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "private, no-store"
        return response
