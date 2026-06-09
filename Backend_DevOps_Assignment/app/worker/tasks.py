import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal
from app.services.anomaly import detect_anomalies
from app.services.cleaner import load_and_clean_transactions
from app.services.llm import classify_transactions, generate_summary
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def _none_if_na(value: object) -> object:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def _decimal_or_none(value: object) -> Decimal | None:
    value = _none_if_na(value)
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _float_or_zero(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _set_failed(db: Session, job: models.Job | None, error: Exception) -> None:
    if job is None:
        return
    job.status = "failed"
    job.error_message = str(error)
    job.completed_at = datetime.utcnow()
    db.commit()


def _classify_uncategorised(db: Session, transactions: list[models.Transaction]) -> None:
    uncategorised = [
        {
            "txn_index": index,
            "transaction": transaction,
            "merchant": transaction.merchant,
            "amount": _float_or_zero(transaction.amount) if transaction.amount is not None else None,
            "currency": transaction.currency,
        }
        for index, transaction in enumerate(transactions)
        if transaction.category == "Uncategorised"
    ]
    if not uncategorised:
        logger.info("No uncategorised transactions to classify")
        return

    payload = [
        {
            "txn_index": item["txn_index"],
            "merchant": item["merchant"],
            "amount": item["amount"],
            "currency": item["currency"],
        }
        for item in uncategorised
    ]
    by_index = {item["txn_index"]: item["transaction"] for item in uncategorised}

    try:
        results, raw_response = classify_transactions(payload)
        for result in results:
            transaction = by_index.get(result["txn_index"])
            if transaction is None:
                continue
            transaction.llm_category = result["category"]
            transaction.category = result["category"]
            transaction.llm_raw_response = raw_response
        db.commit()
        logger.info("Classified %s uncategorised transactions", len(results))
    except Exception as exc:
        logger.warning("LLM classification failed; continuing job: %s", exc)
        for item in uncategorised:
            item["transaction"].llm_failed = True
        db.commit()


def _top_merchants(rows: list[tuple[str | None, object]]) -> list[dict[str, float | str]]:
    merchants = [
        {"merchant": merchant or "Unknown", "total_amount": round(_float_or_zero(total), 2)}
        for merchant, total in rows
    ]
    return merchants[:3]


def _normalise_top_merchants(value: object, fallback: list[dict[str, float | str]]) -> list[dict[str, float | str]]:
    if not isinstance(value, list):
        return fallback

    merchants: list[dict[str, float | str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        merchant = item.get("merchant")
        total_amount = item.get("total_amount")
        try:
            total = round(float(total_amount), 2)
        except (TypeError, ValueError):
            total = 0.0
        merchants.append({"merchant": str(merchant or "Unknown"), "total_amount": total})

    return merchants[:3] or fallback


def _risk_level(anomaly_count: int, total_transactions: int) -> str:
    if total_transactions == 0:
        return "low"
    anomaly_rate = anomaly_count / total_transactions
    if anomaly_count >= 8 or anomaly_rate >= 0.2:
        return "high"
    if anomaly_count >= 3 or anomaly_rate >= 0.08:
        return "medium"
    return "low"


def _fallback_narrative(summary_data: dict) -> str:
    risk = _risk_level(summary_data["anomaly_count"], summary_data["total_transactions"])
    top_merchants = list(summary_data["merchants_by_spend"].keys())[:3]
    merchant_text = ", ".join(top_merchants) if top_merchants else "no dominant merchants"
    return (
        f"Successful spend totals INR {summary_data['total_spend_inr']:.2f} and "
        f"USD {summary_data['total_spend_usd']:.2f}, with highest spend around {merchant_text}. "
        f"{summary_data['anomaly_count']} anomalies were detected, giving this job a {risk} risk profile."
    )


def _create_summary(db: Session, job_id: UUID, total_transactions: int) -> models.JobSummary:
    successful_filters = (
        models.Transaction.job_id == job_id,
        models.Transaction.status == "SUCCESS",
        models.Transaction.amount.isnot(None),
    )
    total_inr = (
        db.query(func.coalesce(func.sum(models.Transaction.amount), 0))
        .filter(*successful_filters, models.Transaction.currency == "INR")
        .scalar()
    )
    total_usd = (
        db.query(func.coalesce(func.sum(models.Transaction.amount), 0))
        .filter(*successful_filters, models.Transaction.currency == "USD")
        .scalar()
    )
    top_rows = (
        db.query(models.Transaction.merchant, func.coalesce(func.sum(models.Transaction.amount), 0))
        .filter(*successful_filters)
        .group_by(models.Transaction.merchant)
        .order_by(func.sum(models.Transaction.amount).desc())
        .limit(5)
        .all()
    )
    category_rows = (
        db.query(models.Transaction.category, func.coalesce(func.sum(models.Transaction.amount), 0))
        .filter(models.Transaction.job_id == job_id, models.Transaction.amount.isnot(None))
        .group_by(models.Transaction.category)
        .all()
    )
    anomaly_count = (
        db.query(func.count(models.Transaction.id))
        .filter(models.Transaction.job_id == job_id, models.Transaction.is_anomaly.is_(True))
        .scalar()
    )

    merchants_by_spend = {
        (merchant or "Unknown"): round(_float_or_zero(total), 2)
        for merchant, total in top_rows
    }
    category_breakdown = {
        (category or "Uncategorised"): round(_float_or_zero(total), 2)
        for category, total in category_rows
    }
    top_merchants = _top_merchants(top_rows)
    summary_data = {
        "total_transactions": total_transactions,
        "total_spend_inr": round(_float_or_zero(total_inr), 2),
        "total_spend_usd": round(_float_or_zero(total_usd), 2),
        "anomaly_count": int(anomaly_count or 0),
        "merchants_by_spend": merchants_by_spend,
        "category_breakdown": category_breakdown,
    }

    summary_payload = {
        "total_spend_inr": summary_data["total_spend_inr"],
        "total_spend_usd": summary_data["total_spend_usd"],
        "top_merchants": top_merchants,
        "anomaly_count": summary_data["anomaly_count"],
        "narrative": _fallback_narrative(summary_data),
        "risk_level": _risk_level(summary_data["anomaly_count"], total_transactions),
    }

    try:
        llm_summary, _raw_response = generate_summary(summary_data)
        summary_payload.update(
            {
                "total_spend_inr": float(llm_summary.get("total_spend_inr", summary_payload["total_spend_inr"])),
                "total_spend_usd": float(llm_summary.get("total_spend_usd", summary_payload["total_spend_usd"])),
                "top_merchants": _normalise_top_merchants(
                    llm_summary.get("top_merchants"),
                    summary_payload["top_merchants"],
                ),
                "anomaly_count": int(llm_summary.get("anomaly_count", summary_payload["anomaly_count"])),
                "narrative": str(llm_summary.get("narrative") or summary_payload["narrative"]),
                "risk_level": str(llm_summary.get("risk_level") or summary_payload["risk_level"]).lower(),
            }
        )
    except Exception as exc:
        logger.warning("LLM summary failed; using local summary: %s", exc)

    if summary_payload["risk_level"] not in {"low", "medium", "high"}:
        summary_payload["risk_level"] = _risk_level(summary_payload["anomaly_count"], total_transactions)

    summary = models.JobSummary(
        job_id=job_id,
        total_spend_inr=Decimal(str(summary_payload["total_spend_inr"])).quantize(Decimal("0.01")),
        total_spend_usd=Decimal(str(summary_payload["total_spend_usd"])).quantize(Decimal("0.01")),
        top_merchants=summary_payload["top_merchants"][:3],
        anomaly_count=summary_payload["anomaly_count"],
        narrative=summary_payload["narrative"],
        risk_level=summary_payload["risk_level"],
    )
    db.add(summary)
    return summary


@celery_app.task(name="process_job")
def process_job(job_id: str, filepath: str) -> None:
    db = SessionLocal()
    job_uuid = UUID(job_id)
    job: models.Job | None = None

    try:
        job = db.query(models.Job).filter(models.Job.id == job_uuid).first()
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        logger.info("Starting job %s", job_id)
        job.status = "processing"
        db.commit()

        logger.info("Job %s: cleaning data", job_id)
        df_clean, raw_count = load_and_clean_transactions(filepath)
        job.row_count_raw = raw_count

        logger.info("Job %s: detecting anomalies", job_id)
        df_clean = detect_anomalies(df_clean)

        logger.info("Job %s: inserting %s transactions", job_id, len(df_clean))
        transactions: list[models.Transaction] = []
        for _, row in df_clean.iterrows():
            transaction = models.Transaction(
                job_id=job_uuid,
                txn_id=_none_if_na(row.get("txn_id")),
                date=_none_if_na(row.get("date")),
                merchant=_none_if_na(row.get("merchant")),
                amount=_decimal_or_none(row.get("amount")),
                currency=_none_if_na(row.get("currency")),
                status=_none_if_na(row.get("status")),
                category=_none_if_na(row.get("category")),
                account_id=_none_if_na(row.get("account_id")),
                is_anomaly=bool(row.get("is_anomaly", False)),
                anomaly_reason=_none_if_na(row.get("anomaly_reason")),
            )
            db.add(transaction)
            transactions.append(transaction)
        db.flush()

        logger.info("Job %s: classifying uncategorised transactions", job_id)
        _classify_uncategorised(db, transactions)

        logger.info("Job %s: creating summary", job_id)
        _create_summary(db, job_uuid, len(transactions))

        job.status = "completed"
        job.completed_at = datetime.utcnow()
        job.row_count_clean = len(transactions)
        job.error_message = None
        db.commit()
        logger.info("Job %s completed", job_id)
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        db.rollback()
        _set_failed(db, job, exc)
        raise
    finally:
        db.close()
        try:
            Path(filepath).unlink(missing_ok=True)
        except Exception:
            logger.warning("Could not remove temporary file %s", filepath)
