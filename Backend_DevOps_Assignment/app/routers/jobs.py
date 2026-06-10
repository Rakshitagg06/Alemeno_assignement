import io
import logging
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app import models, schemas 
from app.database import get_db
from app.worker.tasks import process_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

REQUIRED_COLUMNS = [
    "txn_id",
    "date",
    "merchant",
    "amount",
    "currency",
    "status",
    "category",
    "account_id",
    "notes",
]
VALID_STATUSES = ("pending", "processing", "completed", "failed")


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return float(value)


def _summary_compact(summary: models.JobSummary | None) -> dict | None:
    if summary is None:
        return None
    return {
        "total_spend_inr": _as_float(summary.total_spend_inr),
        "total_spend_usd": _as_float(summary.total_spend_usd),
        "anomaly_count": summary.anomaly_count,
        "risk_level": summary.risk_level,
        "narrative": summary.narrative,
    }


def _summary_full(summary: models.JobSummary) -> dict:
    data = _summary_compact(summary) or {}
    data["top_merchants"] = summary.top_merchants or []
    return data


@router.post("/upload", response_model=schemas.UploadResponse)
async def upload_job(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Uploaded file must be a .csv file")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="CSV file is empty")

    try:
        df = pd.read_csv(io.BytesIO(contents))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse CSV file: {exc}") from exc

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"CSV is missing required columns: {', '.join(missing)}",
        )

    job_id = uuid.uuid4()
    upload_dir = Path("/tmp/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    filepath = upload_dir / f"{job_id}.csv"
    filepath.write_bytes(contents)

    job = models.Job(
        id=job_id,
        filename=file.filename,
        status="pending",
        row_count_raw=len(df),
    )
    db.add(job)
    db.commit()

    logger.info("Job %s received for file %s with %s rows", job_id, file.filename, len(df))
    process_job.delay(str(job_id), str(filepath))

    return {
        "job_id": job_id,
        "status": "pending",
        "message": "Job enqueued successfully",
    }


@router.get(
    "/{job_id}/status",
    response_model=schemas.JobStatusResponse,
    response_model_exclude_unset=True,
)
def get_job_status(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    job = (
        db.query(models.Job)
        .options(joinedload(models.Job.summary))
        .filter(models.Job.id == job_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    response = {
        "job_id": job.id,
        "status": job.status,
        "row_count_raw": job.row_count_raw,
        "row_count_clean": job.row_count_clean,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "error_message": job.error_message,
    }
    if job.status == "completed":
        response["summary"] = _summary_compact(job.summary)
    return response


@router.get("/{job_id}/results", response_model=schemas.JobResultsResponse)
def get_job_results(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    job = (
        db.query(models.Job)
        .options(joinedload(models.Job.summary))
        .filter(models.Job.id == job_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Job not yet completed")
    if job.summary is None:
        raise HTTPException(status_code=500, detail="Completed job is missing summary")

    transactions = (
        db.query(models.Transaction)
        .filter(models.Transaction.job_id == job_id)
        .order_by(models.Transaction.date.asc().nullslast(), models.Transaction.txn_id.asc().nullslast())
        .all()
    )
    anomalies = [txn for txn in transactions if txn.is_anomaly]

    breakdown_rows = (
        db.query(models.Transaction.category, func.sum(models.Transaction.amount))
        .filter(models.Transaction.job_id == job_id, models.Transaction.amount.isnot(None))
        .group_by(models.Transaction.category)
        .all()
    )
    category_breakdown = {
        (category or "Uncategorised"): _as_float(total) or 0.0
        for category, total in breakdown_rows
    }

    return {
        "job_id": job.id,
        "summary": _summary_full(job.summary),
        "anomalies": [
            {
                "txn_id": txn.txn_id,
                "merchant": txn.merchant,
                "amount": _as_float(txn.amount),
                "currency": txn.currency,
                "anomaly_reason": txn.anomaly_reason,
            }
            for txn in anomalies
        ],
        "category_breakdown": category_breakdown,
        "transactions": [
            {
                "txn_id": txn.txn_id,
                "date": txn.date,
                "merchant": txn.merchant,
                "amount": _as_float(txn.amount),
                "currency": txn.currency,
                "status": txn.status,
                "category": txn.category,
                "account_id": txn.account_id,
                "is_anomaly": txn.is_anomaly,
                "anomaly_reason": txn.anomaly_reason,
                "llm_category": txn.llm_category,
                "llm_failed": txn.llm_failed,
            }
            for txn in transactions
        ],
    }


@router.get("", response_model=list[schemas.JobListItem])
def list_jobs(
    status: Literal["pending", "processing", "completed", "failed"] | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    query = db.query(models.Job)
    if status is not None:
        query = query.filter(models.Job.status == status)
    jobs = query.order_by(models.Job.created_at.desc()).all()
    return [
        {
            "job_id": job.id,
            "filename": job.filename,
            "status": job.status,
            "row_count_raw": job.row_count_raw,
            "row_count_clean": job.row_count_clean,
            "created_at": job.created_at,
        }
        for job in jobs
    ]
