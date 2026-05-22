"""HTTP endpoints for the equity-deck agent: upload, status, download."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.services import deck_agent
from app.services.document_parser import parse_upload

router = APIRouter(prefix="/api/deck", tags=["deck"])

# All generated decks go to a single temp directory so the process can find
# them again when the user asks for the download.
OUTPUT_DIR = Path(tempfile.gettempdir()) / "stock-agent-decks"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILES = 8
MAX_TOTAL_BYTES = 40 * 1024 * 1024  # 40 MB combined


@router.post("/generate")
async def generate_deck(
    background_tasks: BackgroundTasks,
    company_name: str = Form(...),
    ticker: str = Form(...),
    files: list[UploadFile] = File(default=[]),
):
    company_name = company_name.strip()
    ticker = ticker.strip().upper()
    if not company_name:
        raise HTTPException(400, "company_name is required.")
    if not ticker:
        raise HTTPException(400, "ticker is required.")
    if not files:
        raise HTTPException(
            400,
            "At least one document (PDF or text transcript) is required.",
        )
    if len(files) > MAX_FILES:
        raise HTTPException(
            400, f"Too many files — limit is {MAX_FILES} per deck."
        )

    # Read uploads into memory and parse text out of them.
    parsed_docs = []
    total_bytes = 0
    for f in files:
        if not f.filename:
            continue
        data = await f.read()
        total_bytes += len(data)
        if total_bytes > MAX_TOTAL_BYTES:
            raise HTTPException(
                413,
                f"Combined upload exceeds {MAX_TOTAL_BYTES // (1024 * 1024)} MB.",
            )
        try:
            parsed_docs.append(parse_upload(f.filename, data))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    if not parsed_docs:
        raise HTTPException(400, "No usable documents were uploaded.")

    job = deck_agent.create_job(company_name, ticker)
    background_tasks.add_task(
        deck_agent.run_job,
        job.job_id,
        company_name,
        ticker,
        parsed_docs,
        OUTPUT_DIR,
    )
    return JSONResponse(
        {
            "job_id": job.job_id,
            "status": job.status,
            "company": company_name,
            "ticker": ticker,
            "documents": [
                {"name": d.filename, "kind": d.kind, "pages": d.pages}
                for d in parsed_docs
            ],
        }
    )


@router.get("/status/{job_id}")
async def deck_status(job_id: str):
    job = deck_agent.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job_id.")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "message": job.message,
        "error": job.error,
        "elapsed_s": round(
            (job.finished_at or 0) - job.started_at, 1
        ) if job.finished_at else None,
        "download_url": f"/api/deck/download/{job.job_id}" if job.status == "done" else None,
    }


@router.get("/download/{job_id}")
async def deck_download(job_id: str):
    job = deck_agent.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job_id.")
    if job.status != "done" or not job.output_path:
        raise HTTPException(409, f"Deck not ready yet (status={job.status}).")
    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(410, "Deck file is no longer available on disk.")
    safe_ticker = "".join(c for c in job.ticker if c.isalnum()) or "DECK"
    return FileResponse(
        path,
        media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        filename=f"{safe_ticker}_study_deck.pptx",
    )
