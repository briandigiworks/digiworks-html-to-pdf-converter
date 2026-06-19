import asyncio
import sys
import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import Response
from pydantic import BaseModel
from playwright.async_api import async_playwright, Browser, Playwright

API_KEY = os.environ.get("API_KEY", "")
if not API_KEY:
    raise RuntimeError("API_KEY environment variable is required")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BrowserState:
    playwright: Optional[Playwright] = None
    browser: Optional[Browser] = None


state = BrowserState()
browser_lock = asyncio.Lock()

LAUNCH_ARGS = [
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-setuid-sandbox",
]


async def launch_browser() -> Browser:
    browser = await state.playwright.chromium.launch(headless=True, args=LAUNCH_ARGS)
    browser.on(
        "disconnected",
        lambda: logger.warning("Chromium browser disconnected unexpectedly"),
    )
    return browser


async def get_browser() -> Browser:
    if state.browser is not None and state.browser.is_connected():
        return state.browser
    async with browser_lock:
        if state.browser is None or not state.browser.is_connected():
            logger.warning("Browser not connected, relaunching Chromium...")
            state.browser = await launch_browser()
    return state.browser


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Launching Chromium browser...")
    state.playwright = await async_playwright().start()
    state.browser = await launch_browser()
    logger.info("Chromium browser ready.")
    yield
    logger.info("Shutting down browser...")
    await state.browser.close()
    await state.playwright.stop()


app = FastAPI(title="HTML to PDF API", version="1.0.0", lifespan=lifespan)


class ConvertRequest(BaseModel):
    html: str
    filename: str = "document.pdf"
    options: dict = {}


@app.get("/health")
async def health():
    browser_ok = state.browser is not None and state.browser.is_connected()
    if not browser_ok:
        raise HTTPException(status_code=503, detail="Browser not ready")
    return {"status": "ok", "browser": "connected"}


@app.post("/convert")
async def convert_html_to_pdf(
    body: ConvertRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    if not body.html or not body.html.strip():
        raise HTTPException(status_code=422, detail="html field must not be empty")

    context = None
    page = None
    try:
        browser = await get_browser()
        context = await browser.new_context()
        page = await context.new_page()

        await page.set_content(body.html, wait_until="networkidle")
        await page.evaluate("() => document.fonts.ready")

        pdf_kwargs = {
            "format": body.options.get("format", "A4"),
            "print_background": body.options.get("print_background", True),
            "margin": body.options.get("margin", {
                "top": "10mm",
                "bottom": "10mm",
                "left": "10mm",
                "right": "10mm",
            }),
        }
        if "landscape" in body.options:
            pdf_kwargs["landscape"] = body.options["landscape"]
        if "scale" in body.options:
            pdf_kwargs["scale"] = body.options["scale"]

        pdf_bytes: bytes = await page.pdf(**pdf_kwargs)

    except Exception as exc:
        logger.exception("PDF generation failed")
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(exc)}")
    finally:
        if page:
            await page.close()
        if context:
            await context.close()

    safe_filename = (
        body.filename.replace('"', "").replace("/", "").replace("\\", "")
    )
    if not safe_filename.endswith(".pdf"):
        safe_filename += ".pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


if __name__ == "__main__":
    import uvicorn
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
