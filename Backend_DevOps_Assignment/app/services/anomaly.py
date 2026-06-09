import pandas as pd

DOMESTIC_ONLY_MERCHANTS = {
    "Swiggy",
    "Ola",
    "IRCTC",
    "Jio Recharge",
    "BookMyShow",
    "Flipkart",
    "Amazon",
    "HDFC ATM",
    "Zomato",
}


def _append_reason(existing: object, reason: str) -> str:
    if existing is None or pd.isna(existing) or str(existing).strip() == "":
        return reason
    return f"{existing}; {reason}"


def detect_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["is_anomaly"] = False
    result["anomaly_reason"] = None

    medians = result.groupby("account_id")["amount"].median(numeric_only=True).to_dict()

    for index, row in result.iterrows():
        amount = row.get("amount")
        account_id = row.get("account_id")
        median = medians.get(account_id)

        if amount is not None and not pd.isna(amount) and median is not None and not pd.isna(median):
            if float(amount) > 3 * float(median):
                reason = f"Amount exceeds 3x account median (median={float(median):.2f})"
                result.at[index, "is_anomaly"] = True
                result.at[index, "anomaly_reason"] = _append_reason(result.at[index, "anomaly_reason"], reason)

        merchant = row.get("merchant")
        currency = row.get("currency")
        if currency == "USD" and merchant in DOMESTIC_ONLY_MERCHANTS:
            reason = "USD transaction for domestic-only merchant"
            result.at[index, "is_anomaly"] = True
            result.at[index, "anomaly_reason"] = _append_reason(result.at[index, "anomaly_reason"], reason)

    return result

