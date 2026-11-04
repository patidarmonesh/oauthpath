"""FastAPI web application — routes, jobs, library per §6.6."""

from __future__ import annotations

import asyncio
import json
import uuid
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, model_validator
from pathlib import Path

from app.config import (
    DATABASE_PATH, PUBLIC_BASE_URL,
    MAX_CONCURRENT_JOBS, JOBS_PER_IP_PER_HOUR,
    JOB_TIMEOUT_SECONDS,
    live_generation_configured, composio_lookup_configured,
)
from app.store import Store
from app.pipeline import run_pipeline
from app.render import render_markdown
from app.schema import JobStatus, GuideRecord

import markdown_it


# ── Models ────────────────────────────────────────────────────────────────

class CreateJobRequest(BaseModel):
    app_name: str = Field(default="", max_length=120)
    feature: str = Field(default="", max_length=300)
    audience: Literal["external customers", "internal team"] = "external customers"
    website: str = Field(default="", max_length=200)
    force: bool = False

    @model_validator(mode="after")
    def validate_inputs(self):
        name = self.app_name.strip()
        site = self.website.strip()
        if not name and not site:
            raise ValueError("app_name or website is required")
        if name and len(name) < 2:
            raise ValueError("app_name must be at least 2 characters")
        return self


# ── State ─────────────────────────────────────────────────────────────────

store: Store = Store(DATABASE_PATH)
job_semaphore: asyncio.Semaphore = None  # type: ignore
active_tasks: dict[str, asyncio.Task] = {}
md_renderer = markdown_it.MarkdownIt("commonmark", {"html": False})
# Disable remote images from markdown per §6.5 rule 5
md_renderer.renderer.rules["image"] = lambda tokens, idx, options, env: tokens[idx].content


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, job_semaphore
    store = Store(DATABASE_PATH)
    store.mark_running_jobs_interrupted()
    job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

    # Import reviewed seeds
    _import_reviewed_seeds()

    yield


app = FastAPI(title="OAuthPath", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    error_id = str(uuid.uuid4())[:8]
    print(f"Unhandled server exception [{error_id}] on {request.method} {request.url.path}: {traceback.format_exc()}")
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={"detail": f"Internal server error (Error ID: {error_id})", "error_id": error_id},
        )
    return HTMLResponse(
        status_code=500,
        content=f"<!DOCTYPE html><html><body><h1>Internal Server Error</h1><p>An unexpected error occurred (Error ID: {error_id}).</p></body></html>",
    )

# Static files and templates
BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _import_reviewed_seeds():
    """Import reviewed JSON seed files from data/reviewed/."""
    reviewed_dir = BASE_DIR.parent / "data" / "reviewed"
    if not reviewed_dir.exists():
        return
    for f in reviewed_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("example") is True or data.get("status") == "example":
                print(f"  Seed skipped: {f.name} is an example fixture")
                continue
            guide = GuideRecord.model_validate(data)
            ok, msg = store.import_reviewed_seed(guide)
            if ok:
                print(f"  Imported seed: {f.name} — {msg}")
            else:
                print(f"  Seed rejected: {f.name} — {msg}")
        except Exception as e:
            print(f"  Seed error: {f.name} — {e}")


def _get_client_ip(request: Request) -> str:
    """Get client IP. Only trust X-Forwarded-For from configured proxies."""
    return request.client.host if request.client else "unknown"


def _render_md(text: str) -> str:
    """Render markdown text to HTML with raw HTML disabled."""
    return md_renderer.render(text)


# ── Routes ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    app_name: str = "",
    feature: str = "",
    audience: str = "",
    website: str = "",
    force: bool = False,
):
    """Library page with form."""
    guides = store.list_guides()
    return templates.TemplateResponse(request=request, name="index.html", context={
        "guides": guides,
        "prefill_app": app_name,
        "prefill_feature": feature,
        "prefill_audience": audience or "external customers",
        "prefill_website": website,
        "prefill_force": "true" if force else "false",
        "live_enabled": live_generation_configured(),
        "base_url": PUBLIC_BASE_URL,
    })


@app.post("/api/jobs")
async def create_job(body: CreateJobRequest, request: Request):
    """Create a generation job (202)."""
    if not live_generation_configured():
        raise HTTPException(503, "Live generation is not configured on this server")

    client_ip = _get_client_ip(request)
    recent = store.count_recent_jobs(client_ip)
    if recent >= JOBS_PER_IP_PER_HOUR:
        raise HTTPException(429, f"Rate limit: max {JOBS_PER_IP_PER_HOUR} jobs per hour")

    if (job_semaphore and job_semaphore.locked()) or len(active_tasks) >= MAX_CONCURRENT_JOBS:
        raise HTTPException(503, "Server is busy — please retry in a few minutes")

    app_name = body.app_name.strip()
    feature = body.feature.strip()
    audience = body.audience
    website = body.website.strip()
    force = body.force

    job_id = str(uuid.uuid4())[:12]
    store.create_job(job_id, app_name, feature, audience, website, client_ip)

    # Start background task
    task = asyncio.create_task(_run_job(job_id, app_name, feature, audience, website, force))
    active_tasks[job_id] = task

    return JSONResponse({"job_id": job_id}, status_code=202)


async def _run_job(job_id: str, app_name: str, feature: str,
                    audience: str, website: str, force: bool):
    """Run a pipeline job in the background."""
    async with job_semaphore:
        try:
            store.update_job(job_id, status=JobStatus.running.value, stage="starting")

            def on_event(msg: str):
                store.add_job_event(job_id, msg)
                store.update_job(job_id, stage=msg[:100])

            guide, error = await asyncio.wait_for(
                run_pipeline(
                    app_name=app_name,
                    feature=feature,
                    audience=audience,
                    website=website,
                    force=force,
                    store=store,
                    on_event=on_event,
                ),
                timeout=JOB_TIMEOUT_SECONDS,
            )

            if error:
                # Check for ambiguous needs_input
                if error.startswith("needs_input:ambiguous:"):
                    candidates_json = error[len("needs_input:ambiguous:"):]
                    store.update_job(
                        job_id,
                        status=JobStatus.needs_input.value,
                        candidates=candidates_json,
                        stage="Awaiting input: ambiguous name",
                    )
                else:
                    error_id = str(uuid.uuid4())[:8]
                    print(f"Job {job_id} error [{error_id}]: {error}")
                    store.update_job(
                        job_id,
                        status=JobStatus.failed.value,
                        error_message=f"Generation failed (Error ID: {error_id})",
                        error_id=error_id,
                    )
            elif guide:
                status = (JobStatus.partial.value
                          if guide.status == "partial"
                          else JobStatus.succeeded.value)
                store.update_job(
                    job_id,
                    status=status,
                    guide_id=guide.id,
                    guide_version=guide.version,
                )
            else:
                error_id = str(uuid.uuid4())[:8]
                store.update_job(
                    job_id,
                    status=JobStatus.failed.value,
                    error_message=f"No guide produced (Error ID: {error_id})",
                    error_id=error_id,
                )

        except asyncio.TimeoutError:
            error_id = str(uuid.uuid4())[:8]
            store.update_job(
                job_id,
                status=JobStatus.failed.value,
                error_message=f"Job timed out (Error ID: {error_id})",
                error_id=error_id,
            )
        except Exception as e:
            error_id = str(uuid.uuid4())[:8]
            # Log the full traceback server-side
            print(f"Job {job_id} exception [{error_id}]: {traceback.format_exc()}")
            store.update_job(
                job_id,
                status=JobStatus.failed.value,
                error_message=f"An error occurred (Error ID: {error_id})",
                error_id=error_id,
            )
        finally:
            active_tasks.pop(job_id, None)


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Job status endpoint for polling."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return JSONResponse({
        "id": job["id"],
        "status": job["status"],
        "stage": job["stage"],
        "events": job["events"],
        "candidates": job["candidates"],
        "error_message": job["error_message"],
        "error_id": job["error_id"],
        "guide_id": job.get("guide_id"),
        "guide_version": job.get("guide_version"),
    })


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str):
    """Progress page."""
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return templates.TemplateResponse(request=request, name="job.html", context={
        "job": job,
        "base_url": PUBLIC_BASE_URL,
    })


@app.get("/guides/{guide_id}/versions/{version}.md")
async def guide_version_markdown(guide_id: str, version: int):
    """Markdown download."""
    guide = store.get_guide(guide_id, version)
    if not guide:
        raise HTTPException(404, "Guide not found")
    md = render_markdown(guide)
    return PlainTextResponse(
        md,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={guide_id}-v{version}.md"},
    )


@app.get("/guides/{guide_id}/versions/{version}", response_class=HTMLResponse)
async def guide_version_page(request: Request, guide_id: str, version: int):
    """Immutable versioned guide page."""
    guide = store.get_guide(guide_id, version)
    if not guide:
        raise HTTPException(404, "Guide not found")

    reviewed = store.get_latest_reviewed(guide_id)
    reviewed_version = reviewed.version if reviewed and reviewed.version != version else None

    guide_html = _render_guide_html(guide)

    return templates.TemplateResponse(request=request, name="guide.html", context={
        "guide": guide,
        "guide_html": guide_html,
        "reviewed_version": reviewed_version,
        "base_url": PUBLIC_BASE_URL,
        "versions": store.get_all_versions(guide_id),
    })


@app.get("/api/guides/{guide_id}/versions/{version}")
async def guide_version_json(guide_id: str, version: int):
    """JSON API endpoint."""
    guide = store.get_guide(guide_id, version)
    if not guide:
        raise HTTPException(404, "Guide not found")
    return JSONResponse(guide.model_dump())


@app.get("/guides/{guide_id}", response_class=HTMLResponse)
async def guide_latest(request: Request, guide_id: str):
    """Redirect to the latest version."""
    guide = store.get_latest_guide(guide_id)
    if not guide:
        raise HTTPException(404, "Guide not found")
    return RedirectResponse(f"/guides/{guide_id}/versions/{guide.version}")


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "live_generation": live_generation_configured(),
        "composio_lookup": composio_lookup_configured(),
    }


def _render_guide_html(guide: GuideRecord) -> dict:
    """Pre-render guide sections to HTML for the template."""
    g = guide.guide

    # Evidence line
    basis_counts = {"documented": 0, "observed": 0, "estimated": 0, "unknown": 0}
    for claim in g.claims:
        basis_counts[claim.basis] = basis_counts.get(claim.basis, 0) + 1
    sources_read = sum(1 for s in guide.sources if s.retrieval_status == "read")

    evidence_parts = [
        f"{basis_counts['documented']} documented",
        f"{basis_counts['observed']} observed",
        f"{basis_counts['estimated']} estimated",
        f"{basis_counts['unknown']} unknown",
        f"{sources_read} sources read",
    ]
    if guide.review.reviewer:
        evidence_parts.append(
            f"Reviewed by {guide.review.reviewer} on {guide.review.reviewed_at}"
        )
    else:
        evidence_parts.append("not human-reviewed")

    from app.render import format_estimate, format_owner

    return {
        "evidence_line": " · ".join(evidence_parts),
        "basis_counts": basis_counts,
        "sources_read": sources_read,
        "format_estimate": format_estimate,
        "format_owner": format_owner,
    }
