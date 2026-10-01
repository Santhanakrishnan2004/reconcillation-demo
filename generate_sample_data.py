"""
Generates realistic-looking sample exports to demo the reconciliation tool:
- jobber_invoices.csv   (mimics Jobber's Invoices Report export)
- quickbooks_export.csv (mimics a QuickBooks Online transaction export)

These are SYNTHETIC — built to reproduce the specific failure patterns real
users report (duplicate synced transactions, amount mismatches from tax/fee
differences, missing counterpart entries), not real client data.
"""
import csv
import random
from datetime import datetime, timedelta

random.seed(42)

clients = [
    "Dave's Plumbing & Rooter", "Green Thumb Landscaping", "Martinez HVAC Services",
    "Sunrise Cleaning Co", "Bay Area Electric", "Clearwater Pool Service",
    "Summit Roofing LLC", "Northside Pest Control", "Allied Appliance Repair",
    "Coastal Tree Care", "Precision Painting Inc", "Rapid Rooter Solutions",
    "Elite Lawn Care", "Metro Window Cleaning", "Desert Air Conditioning",
]

job_types = ["Drain cleaning", "Lawn maintenance", "AC tune-up", "Deep clean",
             "Panel upgrade", "Pool opening", "Roof inspection", "Pest treatment",
             "Appliance repair", "Tree trimming", "Interior paint", "Emergency callout"]

start_date = datetime(2026, 8, 1)
jobber_rows = []
qb_rows = []

invoice_num = 10234

for i in range(60):
    client = random.choice(clients)
    job = random.choice(job_types)
    date = start_date + timedelta(days=random.randint(0, 55))
    amount = round(random.uniform(120, 2400), 2)
    invoice_num += 1
    inv_id = f"INV-{invoice_num}"
    status = random.choices(["Paid", "Sent", "Overdue"], weights=[70, 20, 10])[0]

    jobber_rows.append({
        "Invoice #": inv_id,
        "Client": client,
        "Job": job,
        "Invoice Date": date.strftime("%Y-%m-%d"),
        "Amount": amount,
        "Status": status,
    })

    # Simulate real-world sync failure patterns:
    roll = random.random()
    if roll < 0.12:
        # MISSING: never made it to QuickBooks at all (sync failure)
        continue
    elif roll < 0.22:
        # DUPLICATE: synced twice (classic Jobber<->QBO complaint)
        for _ in range(2):
            qb_rows.append({
                "Date": date.strftime("%m/%d/%Y"),
                "Transaction Type": "Invoice",
                "Num": inv_id,
                "Name": client,
                "Memo": job,
                "Amount": amount,
            })
        continue
    elif roll < 0.35:
        # MISMATCH: amount differs slightly (tax/fee/rounding handled differently)
        drift = round(random.choice([-1, 1]) * random.uniform(5, 45), 2)
        qb_rows.append({
            "Date": date.strftime("%m/%d/%Y"),
            "Transaction Type": "Invoice",
            "Num": inv_id,
            "Name": client,
            "Memo": job,
            "Amount": round(amount + drift, 2),
        })
    elif roll < 0.40:
        # DATE MISMATCH: synced a day or two late
        qb_date = date + timedelta(days=random.randint(1, 3))
        qb_rows.append({
            "Date": qb_date.strftime("%m/%d/%Y"),
            "Transaction Type": "Invoice",
            "Num": inv_id,
            "Name": client,
            "Memo": job,
            "Amount": amount,
        })
    else:
        # CLEAN MATCH — except ~1 in 8 where the description was edited
        # on one side after sync (shows up when comparing Job <-> Memo)
        memo = job
        if random.random() < 0.125:
            memo = random.choice([j for j in job_types if j != job] + [""])
        qb_rows.append({
            "Date": date.strftime("%m/%d/%Y"),
            "Transaction Type": "Invoice",
            "Num": inv_id,
            "Name": client,
            "Memo": memo,
            "Amount": amount,
        })

# A couple of QBO-only entries too (manual journal entries never in Jobber)
for _ in range(3):
    date = start_date + timedelta(days=random.randint(0, 55))
    qb_rows.append({
        "Date": date.strftime("%m/%d/%Y"),
        "Transaction Type": "Journal Entry",
        "Num": f"JE-{random.randint(100,999)}",
        "Name": random.choice(clients),
        "Memo": "Manual adjustment",
        "Amount": round(random.uniform(-200, 200), 2),
    })

with open("jobber_invoices.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["Invoice #", "Client", "Job", "Invoice Date", "Amount", "Status"])
    w.writeheader()
    w.writerows(jobber_rows)

with open("quickbooks_export.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["Date", "Transaction Type", "Num", "Name", "Memo", "Amount"])
    w.writeheader()
    w.writerows(qb_rows)

print(f"Generated {len(jobber_rows)} Jobber invoices and {len(qb_rows)} QuickBooks transactions.")
