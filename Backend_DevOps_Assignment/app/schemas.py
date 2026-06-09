from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UploadResponse(BaseModel):
    job_id: UUID
    status: str
    message: str


class JobSummaryCompact(BaseModel):
    total_spend_inr: float | None
    total_spend_usd: float | None
    anomaly_count: int | None
    risk_level: str | None
    narrative: str | None


class JobStatusResponse(BaseModel):
    job_id: UUID
    status: str
    row_count_raw: int | None
    row_count_clean: int | None
    created_at: datetime
    completed_at: datetime | None
    error_message: str | None
    summary: JobSummaryCompact | None = None


class JobSummaryFull(JobSummaryCompact):
    top_merchants: list[dict[str, Any]] | None


class AnomalyResponse(BaseModel):
    txn_id: str | None
    merchant: str | None
    amount: float | None
    currency: str | None
    anomaly_reason: str | None


class TransactionResponse(BaseModel):
    txn_id: str | None
    date: date | None
    merchant: str | None
    amount: float | None
    currency: str | None
    status: str | None
    category: str | None
    account_id: str | None
    is_anomaly: bool
    anomaly_reason: str | None
    llm_category: str | None
    llm_failed: bool


class JobResultsResponse(BaseModel):
    job_id: UUID
    summary: JobSummaryFull
    anomalies: list[AnomalyResponse]
    category_breakdown: dict[str, float]
    transactions: list[TransactionResponse]


class JobListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    filename: str
    status: str
    row_count_raw: int | None
    row_count_clean: int | None
    created_at: datetime

