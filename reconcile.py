"""
Core reconciliation engine.

Takes a Jobber invoice export + a QuickBooks transaction export and flags
the four failure patterns that show up repeatedly in real user complaints:

  1. DUPLICATE    — the same Jobber invoice synced into QuickBooks more than once
  2. MISSING       — a Jobber invoice with no matching QuickBooks entry at all
  3. AMOUNT_MISMATCH — matched by invoice #, but the dollar amount differs
  4. DATE_MISMATCH   — matched by invoice #, amount agrees, but date differs
  5. QB_ONLY         — a QuickBooks entry with no Jobber counterpart
  6. FIELD_MISMATCH  — matched, but a user-chosen extra column disagrees
                       (e.g. Jobber "Job" vs QuickBooks "Memo")

Matching strategy: match primarily on invoice number (Jobber "Invoice #" vs
QuickBooks "Num" — this is the join key real users already have, since Jobber
writes its invoice number into the QBO "Num" field on sync). Where invoice
numbers don't line up cleanly (common when someone manually entered something,
or an old CSV uses a different numbering convention), fall back to fuzzy
matching on client name + amount + date proximity.
"""
import pandas as pd
from rapidfuzz import fuzz
from datetime import datetime


def _parse_date(s):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(s).strip(), fmt)
        except ValueError:
            continue
    parsed = pd.to_datetime(s, errors="coerce")
    return parsed.to_pydatetime() if pd.notna(parsed) else pd.NaT


def _to_amount(series: pd.Series) -> pd.Series:
    """Parse '$1,810.74', '(200.00)' and plain numbers into floats."""
    s = series.astype(str).str.strip()
    negative = s.str.startswith("(") & s.str.endswith(")")
    s = s.str.replace(r"[$,()\s]", "", regex=True)
    out = pd.to_numeric(s, errors="coerce")
    return out.where(~negative, -out)


COMPARE_MODES = ["Text (exact)", "Text (fuzzy)", "Number", "Date"]


def _norm_text(v) -> str:
    return "" if pd.isna(v) else " ".join(str(v).lower().split())


def _values_differ(a, b, mode: str) -> bool:
    """True if two cell values should be flagged as disagreeing under `mode`."""
    if pd.isna(a) and pd.isna(b):
        return False
    if mode == "Number":
        na, nb = _to_amount(pd.Series([a, b])).tolist()
        if pd.isna(na) or pd.isna(nb):
            return not (pd.isna(na) and pd.isna(nb))
        return abs(na - nb) >= 0.01
    if mode == "Date":
        da, db = _parse_date(a), _parse_date(b)
        if pd.isna(da) or pd.isna(db):
            return not (pd.isna(da) and pd.isna(db))
        return da.date() != db.date()
    if mode == "Text (fuzzy)":
        return fuzz.token_sort_ratio(_norm_text(a), _norm_text(b)) < 85
    return _norm_text(a) != _norm_text(b)


def reconcile(
    jobber_df: pd.DataFrame,
    qb_df: pd.DataFrame,
    extra_pairs: list[dict] | None = None,
    amount_tolerance: float = 1.0,
    date_tolerance_days: int = 0,
) -> dict:
    """
    extra_pairs: optional user-chosen column pairs to compare on every matched
    invoice, each {"label", "jobber_col", "qb_col", "mode"} where mode is one
    of COMPARE_MODES and the columns exist in jobber_df / qb_df respectively.
    """
    jobber = jobber_df.copy()
    qb = qb_df.copy()
    extra_pairs = extra_pairs or []

    def field_issues(jrow, qrow, inv):
        out = []
        for pair in extra_pairs:
            a, b = jrow[pair["jobber_col"]], qrow[pair["qb_col"]]
            if _values_differ(a, b, pair["mode"]):
                show = lambda v: "(blank)" if pd.isna(v) else str(v)
                out.append({
                    "type": "FIELD_MISMATCH",
                    "invoice": inv,
                    "client": jrow["Client"],
                    "jobber_amount": jrow["_amount"],
                    "qb_amount": qrow["_amount"],
                    "detail": f"{pair['label']}: Jobber '{show(a)}' vs QuickBooks '{show(b)}'.",
                })
        return out

    jobber["_date"] = jobber["Invoice Date"].apply(_parse_date)
    qb["_date"] = qb["Date"].apply(_parse_date)
    qb["_amount"] = _to_amount(qb["Amount"])
    jobber["_amount"] = _to_amount(jobber["Amount"])

    issues = []
    matched_qb_idx = set()

    # --- Pass 1: exact invoice-number join, find duplicates ---
    qb_by_num = {}
    for idx, row in qb.iterrows():
        qb_by_num.setdefault(str(row["Num"]).strip(), []).append(idx)

    for _, jrow in jobber.iterrows():
        inv = str(jrow["Invoice #"]).strip()
        candidates = qb_by_num.get(inv, [])

        if len(candidates) == 0:
            # fall back to fuzzy match before declaring it missing
            best_idx, best_score = None, 0
            # Only rows with a close amount can match, so filter on that first
            # (vectorized) instead of fuzzy-scoring every QuickBooks row.
            j_amt = 0 if pd.isna(jrow["_amount"]) else jrow["_amount"]
            close = qb.index[(qb["_amount"].fillna(0) - j_amt).abs() < max(amount_tolerance, 0.01)]
            for idx in close:
                if idx in matched_qb_idx:
                    continue
                qrow = qb.loc[idx]
                name_score = fuzz.token_sort_ratio(str(jrow["Client"]), str(qrow["Name"]))
                date_close = (
                    pd.notna(jrow["_date"]) and pd.notna(qrow["_date"])
                    and abs((jrow["_date"] - qrow["_date"]).days) <= max(5, date_tolerance_days)
                )
                if name_score > 80 and date_close:
                    if name_score > best_score:
                        best_idx, best_score = idx, name_score
            if best_idx is not None:
                matched_qb_idx.add(best_idx)
                issues.extend(field_issues(jrow, qb.loc[best_idx], inv))
                continue  # matched via fallback
            issues.append({
                "type": "MISSING",
                "invoice": inv,
                "client": jrow["Client"],
                "jobber_amount": jrow["_amount"],
                "qb_amount": None,
                "detail": "No matching QuickBooks entry found by invoice # or fuzzy name/amount/date.",
            })

        elif len(candidates) > 1:
            total = len(candidates)
            issues.append({
                "type": "DUPLICATE",
                "invoice": inv,
                "client": jrow["Client"],
                "jobber_amount": jrow["_amount"],
                "qb_amount": qb.loc[candidates[0], "_amount"],
                "detail": f"Invoice {inv} appears {total}x in QuickBooks — likely synced twice.",
            })
            for idx in candidates:
                matched_qb_idx.add(idx)

        else:
            idx = candidates[0]
            matched_qb_idx.add(idx)
            qrow = qb.loc[idx]
            amt_diff = round((jrow["_amount"] or 0) - (qrow["_amount"] or 0), 2)
            if abs(amt_diff) >= max(amount_tolerance, 0.01):
                issues.append({
                    "type": "AMOUNT_MISMATCH",
                    "invoice": inv,
                    "client": jrow["Client"],
                    "jobber_amount": jrow["_amount"],
                    "qb_amount": qrow["_amount"],
                    "detail": f"Jobber shows ${jrow['_amount']:.2f}, QuickBooks shows ${qrow['_amount']:.2f} (diff ${amt_diff:+.2f}).",
                })
            elif (
                pd.notna(jrow["_date"]) and pd.notna(qrow["_date"])
                and abs((jrow["_date"] - qrow["_date"]).days) > date_tolerance_days
            ):
                issues.append({
                    "type": "DATE_MISMATCH",
                    "invoice": inv,
                    "client": jrow["Client"],
                    "jobber_amount": jrow["_amount"],
                    "qb_amount": qrow["_amount"],
                    "detail": f"Jobber date {jrow['_date'].date()} vs QuickBooks date {qrow['_date'].date()}.",
                })
            issues.extend(field_issues(jrow, qrow, inv))

    # --- Pass 2: QuickBooks-only entries (never existed in Jobber) ---
    for idx, qrow in qb.iterrows():
        if idx in matched_qb_idx:
            continue
        txn_type = qrow.get("Transaction Type")
        kind = f"QuickBooks entry ({txn_type})" if pd.notna(txn_type) else "QuickBooks entry"
        issues.append({
            "type": "QB_ONLY",
            "invoice": qrow["Num"],
            "client": qrow["Name"],
            "jobber_amount": None,
            "qb_amount": qrow["_amount"],
            "detail": f"{kind} with no matching Jobber invoice — likely a manual entry worth reviewing.",
        })

    issues_df = pd.DataFrame(
        issues, columns=["type", "invoice", "client", "jobber_amount", "qb_amount", "detail"]
    )
    jobber_side = ["DUPLICATE", "MISSING", "AMOUNT_MISMATCH", "DATE_MISMATCH", "FIELD_MISMATCH"]
    summary = {
        "total_jobber_invoices": len(jobber),
        "total_qb_transactions": len(qb),
        "clean_matches": len(jobber)
        - issues_df.loc[issues_df["type"].isin(jobber_side), "invoice"].nunique(),
        "issues_by_type": issues_df["type"].value_counts().to_dict() if not issues_df.empty else {},
        "likely_dollar_impact": round(
            issues_df.loc[issues_df["type"] == "AMOUNT_MISMATCH", "jobber_amount"].sub(
                issues_df.loc[issues_df["type"] == "AMOUNT_MISMATCH", "qb_amount"]
            ).abs().sum()
            + issues_df.loc[issues_df["type"] == "DUPLICATE", "jobber_amount"].sum()
            if not issues_df.empty else 0,
            2,
        ),
    }

    return {"issues": issues_df, "summary": summary}


if __name__ == "__main__":
    jobber_df = pd.read_csv("jobber_invoices.csv")
    qb_df = pd.read_csv("quickbooks_export.csv")
    result = reconcile(jobber_df, qb_df)
    print("Summary:", result["summary"])
    print()
    print(result["issues"].to_string(index=False))
