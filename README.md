# Books Match — Jobber + QuickBooks Reconciliation Demo

A working prototype that catches the exact sync failures Jobber/QuickBooks
users report: duplicate transactions, amount mismatches, date drift, and
invoices that silently never synced.

## Run it locally
```
pip install -r requirements.txt
python generate_sample_data.py   # creates sample CSVs to demo with
streamlit run app.py
```

## Deploy it for free (so you have a real shareable link)
1. Push this folder to a new GitHub repo
2. Go to share.streamlit.io, sign in with GitHub, "New app"
3. Point it at this repo, main file = app.py
4. You'll get a public URL like yourname-booksmatch.streamlit.app —
   this is what you share in the Jobber community / forum posts.

## How the matching works (reconcile.py)
1. Primary match: Jobber "Invoice #" against QuickBooks "Num" (this is the
   join key that already exists post-sync — Jobber writes its invoice
   number into the QBO Num field).
2. Fallback: fuzzy match on client name + amount + date proximity, for
   cases where invoice numbers don't align cleanly.
3. Flags: DUPLICATE (invoice synced 2x+), MISSING (never synced),
   AMOUNT_MISMATCH, DATE_MISMATCH, QB_ONLY (manual entries with no
   Jobber counterpart), FIELD_MISMATCH (a user-chosen extra column
   disagrees).

## Column mapping (column_mapping.py)
- Upload CSV or Excel. QuickBooks report title rows and TOTAL rows are
  skipped automatically.
- The 4 core fields (invoice #, client, date, amount) are auto-guessed
  from the headers and can be overridden per file.
- "Compare more columns": pair any Jobber column with any QuickBooks
  column (e.g. Job <-> Memo, Status <-> Status) and pick how to compare
  (exact text, fuzzy text, number, date). Related pairs are suggested
  automatically from header names and overlapping values.
- Matching rules: amount tolerance ($) and date tolerance (days).

## What's real vs. a stand-in right now
- The matching logic is real and tested against realistic data.
- generate_sample_data.py creates SYNTHETIC sample data reproducing the
  failure patterns real users report — not real client data. Once you
  have a real export from a real user, the tool reads it the same way;
  the column-mapping step handles differently named headers.

## Honest status
This is a working demo, not a finished product. No auth, no database, no
payment flow. The point right now is: does this actually catch real
problems for real users, and will anyone care? That gets answered before
any of the rest gets built.
