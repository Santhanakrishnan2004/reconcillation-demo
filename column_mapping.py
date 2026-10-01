"""
Column-mapping helper.

Real exports from Jobber (and especially QuickBooks, which varies by region/
version/report customization) won't always use the exact column names our
demo sample data uses. This module guesses the best match for each required
field from whatever columns are actually in the uploaded file, so the UI can
show the user a pre-filled mapping screen to confirm or correct — rather than
silently failing or forcing them to rename their CSV headers by hand.

Canonical fields we need, per side:
  Jobber: invoice_number, client, date, amount
  QuickBooks: invoice_number, name, date, amount
"""
from rapidfuzz import fuzz

# Each canonical field maps to a list of common real-world header variants.
# These are drawn from Jobber's own export docs and common QBO export/report
# column names (Online + Desktop variants, different regions use "Num"/"Ref No."
# interchangeably, etc.)
FIELD_ALIASES = {
    "invoice_number": [
        "Invoice #", "Invoice Number", "Invoice No", "Inv #", "InvoiceNum",
        "Num", "Ref No.", "Reference No", "Doc Number", "Document Number", "Transaction ID",
    ],
    "client": [
        "Client", "Customer", "Customer Name", "Name", "Client Name", "Payee",
    ],
    "date": [
        "Date", "Invoice Date", "Txn Date", "Transaction Date", "Create Date", "Posting Date",
    ],
    "amount": [
        "Amount", "Total", "Invoice Amount", "Amount Due", "Debit", "Open Balance", "Line Amount",
    ],
}


def guess_mapping(columns: list[str]) -> dict:
    """
    For each canonical field, return the best-matching real column name
    (or None if nothing scores well enough to guess confidently).
    Uses fuzzy string matching so close variants (e.g. "Inv. #" vs "Invoice #")
    still match without an exact alias list entry.
    """
    guesses = {}
    used = set()
    for field, aliases in FIELD_ALIASES.items():
        best_col, best_score = None, 0
        for col in columns:
            if col in used:
                continue
            for alias in aliases:
                score = fuzz.token_sort_ratio(str(col).lower(), alias.lower())
                if score > best_score:
                    best_col, best_score = col, score
        # Require a reasonably confident match before auto-filling —
        # otherwise leave it blank and make the user pick.
        if best_score >= 70:
            guesses[field] = best_col
            used.add(best_col)
        else:
            guesses[field] = None
    return guesses


def apply_mapping(df, mapping: dict, rename_to: dict):
    """
    Renames df's columns per the user-confirmed mapping, using rename_to to
    map each canonical field name to the column name reconcile.py expects
    (e.g. {"invoice_number": "Invoice #", "client": "Client", ...}).
    Raises a clear error if a required field wasn't mapped to anything.
    """
    missing = [f for f, col in mapping.items() if col is None and f in rename_to]
    if missing:
        raise ValueError(
            f"These required fields aren't mapped to a column yet: {', '.join(missing)}. "
            "Pick a column for each before running the check."
        )
    rename = {mapping[field]: target for field, target in rename_to.items() if mapping.get(field)}
    # Drop unmapped columns that already use a target name, so renaming
    # can't produce two columns called e.g. "Client".
    clashes = [c for c in df.columns if c in rename.values() and c not in rename]
    return df.drop(columns=clashes).rename(columns=rename)


# Extra columns that commonly mean the same thing across the two systems,
# even though the header names look nothing alike.
EXTRA_PAIR_ALIASES = [
    ({"job", "job title", "description", "service", "line item"}, {"memo", "description", "memo/description", "product/service"}),
    ({"status", "invoice status"}, {"status", "payment status", "open balance"}),
    ({"email", "client email"}, {"email", "customer email"}),
    ({"due date"}, {"due date"}),
    ({"tax", "tax amount"}, {"tax", "tax amount", "sales tax"}),
]


def _guess_mode(series) -> str:
    """Pick a sensible comparison mode from a column's values."""
    import pandas as pd
    s = series.dropna().astype(str).head(50)
    if s.empty:
        return "Text (exact)"
    if pd.to_numeric(s.str.replace(r"[$,]", "", regex=True), errors="coerce").notna().mean() > 0.9:
        return "Number"
    if pd.to_datetime(s, errors="coerce", format="mixed").notna().mean() > 0.9:
        return "Date"
    return "Text (exact)"


def suggest_extra_pairs(jobber_df, qb_df, used_jobber: set, used_qb: set) -> list[dict]:
    """
    Suggest pairs of leftover (not core-mapped) columns that probably hold the
    same information on both sides. Scores each candidate pair by header-name
    similarity, known aliases, and how much the actual values overlap — so
    "Job" <-> "Memo" is found from the data even though the names differ.
    """
    j_cols = [c for c in jobber_df.columns if c not in used_jobber]
    q_cols = [c for c in qb_df.columns if c not in used_qb]
    candidates = []
    for jc in j_cols:
        j_vals = set(jobber_df[jc].dropna().astype(str).str.strip().str.lower())
        for qc in q_cols:
            name_score = fuzz.token_sort_ratio(str(jc).lower(), str(qc).lower())
            alias_hit = any(
                str(jc).lower() in js and str(qc).lower() in qs for js, qs in EXTRA_PAIR_ALIASES
            )
            q_vals = set(qb_df[qc].dropna().astype(str).str.strip().str.lower())
            overlap = len(j_vals & q_vals) / max(1, min(len(j_vals), len(q_vals)))
            score = max(name_score, 90 if alias_hit else 0, overlap * 100)
            if score >= 60:
                candidates.append((score, jc, qc))

    pairs, taken_j, taken_q = [], set(), set()
    for score, jc, qc in sorted(candidates, reverse=True):
        if jc in taken_j or qc in taken_q:
            continue
        taken_j.add(jc)
        taken_q.add(qc)
        pairs.append({
            "label": jc if jc == qc else f"{jc} ↔ {qc}",
            "jobber_col": jc,
            "qb_col": qc,
            "mode": _guess_mode(jobber_df[jc]),
        })
    return pairs


def load_table(file):
    """
    Read an uploaded CSV or Excel export into a DataFrame.

    QuickBooks report exports usually start with a few title rows
    ("Company Name", "Transaction List", "August 2026", blank...) before the
    real header row, so we read raw and use the first row that's about as
    wide as the table as the header.
    """
    import pandas as pd
    name = getattr(file, "name", str(file)).lower()
    if name.endswith((".xlsx", ".xls")):
        raw = pd.read_excel(file, header=None, dtype=str)
    else:
        import csv
        import io
        data = file.getvalue() if hasattr(file, "getvalue") else open(file, "rb").read()
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        rows = list(csv.reader(io.StringIO(text)))
        width = max((len(r) for r in rows), default=0)
        raw = pd.DataFrame([r + [None] * (width - len(r)) for r in rows])
        raw = raw.replace(r"^\s*$", None, regex=True)
    raw = raw.dropna(how="all", axis=1)
    widths = raw.notna().sum(axis=1)
    header_idx = int((widths >= widths.max() * 0.6).idxmax())
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = [str(c).strip() if pd.notna(c) else f"Column {i + 1}"
                  for i, c in enumerate(raw.iloc[header_idx])]
    df = df.dropna(how="all").reset_index(drop=True)
    # Drop QuickBooks "TOTAL" footer rows
    first = df.columns[0]
    df = df[~df[first].astype(str).str.strip().str.upper().str.startswith("TOTAL")]
    return df.reset_index(drop=True)
