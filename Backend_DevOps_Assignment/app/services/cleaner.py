from datetime import date

import pandas as pd


def _blank_to_none(value: object) -> object:
    if pd.isna(value):
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def _parse_date(value: object) -> date | None:
    value = _blank_to_none(value)
    if value is None:
        return None

    text = str(value).strip()
    for fmt in ("%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d"):
        parsed = pd.to_datetime(text, format=fmt, errors="coerce")
        if not pd.isna(parsed):
            return parsed.date()
    return None


def _parse_amount(value: object) -> float | None:
    value = _blank_to_none(value)
    if value is None:
        return None

    text = str(value).strip()
    if text.startswith("$"):
        text = text[1:]
    text = text.replace(",", "")
    amount = pd.to_numeric(text, errors="coerce")
    if pd.isna(amount):
        return None
    return float(amount)


def _normalise_string(value: object, uppercase: bool = False) -> str | None:
    value = _blank_to_none(value)
    if value is None:
        return None

    text = str(value).strip()
    return text.upper() if uppercase else text


def load_and_clean_transactions(filepath: str) -> tuple[pd.DataFrame, int]:
    df = pd.read_csv(filepath, keep_default_na=False)
    raw_count = len(df)

    df["date"] = df["date"].apply(_parse_date)
    df["amount"] = df["amount"].apply(_parse_amount)
    df["currency"] = df["currency"].apply(lambda value: _normalise_string(value, uppercase=True))
    df["status"] = df["status"].apply(lambda value: _normalise_string(value, uppercase=True))
    df["category"] = df["category"].apply(
        lambda value: "Uncategorised" if _blank_to_none(value) is None else str(value).strip()
    )

    for column in ["txn_id", "merchant", "account_id", "notes"]:
        df[column] = df[column].apply(_blank_to_none)

    df = df.drop_duplicates(keep="first").reset_index(drop=True)
    return df, raw_count

