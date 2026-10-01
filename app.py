import hashlib
import html

import altair as alt
import pandas as pd
import streamlit as st

from column_mapping import apply_mapping, guess_mapping, load_table, suggest_extra_pairs
from reconcile import COMPARE_MODES, reconcile

st.set_page_config(
    page_title="Books Match — Jobber ↔ QuickBooks", page_icon="🧾", layout="wide"
)

# Set to a mailto: or booking link to show a "talk to us" button under the results.
CONTACT_LINK = "https://santhanakris.gumroad.com/coffee"

SIDE_A_RENAME_TO = {
    "invoice_number": "Invoice #",
    "client": "Client",
    "date": "Invoice Date",
    "amount": "Amount",
}
SIDE_B_RENAME_TO = {
    "invoice_number": "Num",
    "client": "Name",
    "date": "Date",
    "amount": "Amount",
}
FIELD_LABELS = {
    "invoice_number": "Invoice / reference #",
    "client": "Client / customer",
    "date": "Date",
    "amount": "Amount",
}
NOT_IN_FILE = "— not in this file —"

ISSUE_META = {
    "DUPLICATE": (
        "🔁",
        "Duplicate sync",
        "#E5484D",
        "Same invoice landed in QuickBooks more than once — revenue is overstated.",
    ),
    "MISSING": (
        "❌",
        "Never synced",
        "#F76B15",
        "Invoice exists in Jobber but QuickBooks has no record of it.",
    ),
    "AMOUNT_MISMATCH": (
        "💲",
        "Amount mismatch",
        "#FFB224",
        "Same invoice, different dollar amount — often fees, tax or edits after sync.",
    ),
    "DATE_MISMATCH": (
        "📅",
        "Date drift",
        "#8E4EC6",
        "Amounts agree but dates don't — can push revenue into the wrong period.",
    ),
    "FIELD_MISMATCH": (
        "🧩",
        "Column mismatch",
        "#3E63DD",
        "A column you chose to compare disagrees between the two systems.",
    ),
    "QB_ONLY": (
        "❓",
        "Only in QuickBooks",
        "#6B7B73",
        "Entries with no Jobber counterpart — manual adjustments worth a look.",
    ),
}

# ---------------------------------------------------------------- styles
st.markdown(
    """
<style>
.block-container {padding-top: 2rem; max-width: 1200px;}
.hero {
  background: linear-gradient(135deg, #0B3D2E 0%, #0E9F6E 60%, #34D399 100%);
  border-radius: 22px; padding: 2.4rem 2.6rem; color: #fff; margin-bottom: 1.6rem;
  box-shadow: 0 12px 32px rgba(14,159,110,.25);
}
.hero h1 {color:#fff; font-size: 2.6rem; margin: 0 0 .4rem 0; letter-spacing:-.02em;}
.hero p.lead {font-size: 1.15rem; opacity: .95; margin: 0 0 1.2rem 0; max-width: 720px;}
.hero .pill {display:inline-block; background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.3);
  padding: .3rem .8rem; border-radius: 999px; font-size: .85rem; margin: 0 .4rem .4rem 0;}
.step {display:flex; align-items:center; gap:.8rem; margin: 1.8rem 0 .6rem 0;}
.step .num {width:34px; height:34px; border-radius:50%; background:#0E9F6E; color:#fff; font-weight:700;
  display:flex; align-items:center; justify-content:center; flex-shrink:0;}
.step .title {font-size:1.35rem; font-weight:700; color:#14211B;}
.step .sub {font-size:.92rem; color:#5B6B63;}
.links {background:#fff; border:1px solid #DCE7E1; border-radius:16px; padding:1rem 1.2rem;}
.link-row {display:grid; grid-template-columns: 1fr 70px 1fr; align-items:center; margin:.35rem 0;}
.chip {padding:.4rem .75rem; border-radius:10px; font-size:.9rem; font-weight:600;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.chip.j {background:#E6F6EF; color:#0B3D2E; border:1px solid #B7E4CF;}
.chip.q {background:#E8F0FE; color:#1A3A8A; border:1px solid #C3D4F7; text-align:right;}
.chip.extra.j {background:#FFF7E0; border-color:#F5DFA0; color:#6B4E00;}
.chip.extra.q {background:#FFF7E0; border-color:#F5DFA0; color:#6B4E00;}
.chip.none {background:#F3F4F3; color:#9AA59F; border:1px dashed #C9D1CD; font-weight:500;}
.connector {text-align:center; color:#0E9F6E; font-weight:700; font-size:1.1rem;}
.connector.extra {color:#C28A00;}
.links .head {display:grid; grid-template-columns: 1fr 70px 1fr; font-size:.78rem; text-transform:uppercase;
  letter-spacing:.06em; color:#7A8A82; margin-bottom:.3rem;}
.kpi {background:#fff; border:1px solid #DCE7E1; border-radius:16px; padding:1.1rem 1.2rem; height:100%;}
.kpi .label {font-size:.82rem; color:#5B6B63; text-transform:uppercase; letter-spacing:.05em;}
.kpi .value {font-size:2rem; font-weight:800; color:#14211B; line-height:1.2;}
.kpi .hint {font-size:.82rem; color:#7A8A82;}
.kpi.alert .value {color:#E5484D;}
.health {background:#fff; border:1px solid #DCE7E1; border-radius:16px; padding:1.2rem; display:flex;
  align-items:center; gap:1.2rem; height:100%;}
.ring {width:116px; height:116px; border-radius:50%; display:flex; align-items:center; justify-content:center; flex-shrink:0;}
.ring .inner {width:90px; height:90px; border-radius:50%; background:#fff; display:flex; flex-direction:column;
  align-items:center; justify-content:center;}
.ring .pct {font-size:1.6rem; font-weight:800; color:#14211B;}
.ring .cap {font-size:.7rem; color:#7A8A82; text-transform:uppercase;}
.issue-card {background:#fff; border:1px solid #DCE7E1; border-left:5px solid var(--c); border-radius:14px;
  padding:.85rem 1rem; margin-bottom:.7rem;}
.issue-card .t {font-weight:700; color:#14211B;}
.issue-card .n {float:right; font-weight:800; color:var(--c); font-size:1.2rem;}
.issue-card .d {font-size:.86rem; color:#5B6B63; margin-top:.2rem;}
.cta {background:#0B3D2E; color:#E6F6EF; border-radius:18px; padding:1.4rem 1.6rem; margin-top:1.6rem;}
.cta b {color:#fff;}
div[data-testid="stFileUploader"] section {border-radius:14px;}
</style>
""",
    unsafe_allow_html=True,
)


def step(num, title, sub=""):
    st.markdown(
        f'<div class="step"><div class="num">{num}</div><div><div class="title">{title}</div>'
        f'<div class="sub">{sub}</div></div></div>',
        unsafe_allow_html=True,
    )


def kpi(label, value, hint="", alert=False):
    return (
        f'<div class="kpi{" alert" if alert else ""}"><div class="label">{label}</div>'
        f'<div class="value">{value}</div><div class="hint">{hint}</div></div>'
    )


def chip(text, side, extra=False):
    if not text:
        return '<div class="chip none">not mapped</div>'
    return f'<div class="chip {side}{" extra" if extra else ""}" title="{html.escape(text)}">{html.escape(text)}</div>'


# ---------------------------------------------------------------- hero
st.markdown(
    """
<div class="hero">
  <h1>🧾 Books Match</h1>
  <p class="lead">Drop in your Jobber and QuickBooks exports. In under a minute you'll see every duplicate,
  missing invoice, and amount that doesn't add up, so you don't have to hunt for them one by one.</p>
  <span class="pill">⚡ Results in seconds</span>
  <span class="pill">🧠 Works with your column names</span>
  <span class="pill">🔒 Processed in memory, nothing stored</span>
  <span class="pill">💸 Free, no signup</span>
  <span class="pill">🔄 Also works with ServiceTitan, Housecall Pro, Square & Stripe exports</span>
</div>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------- step 1: upload
step(
    1,
    "Bring your two exports",
    "CSV or Excel. Jobber's Invoices report and QuickBooks' Transaction List both work.",
)

if "use_sample" not in st.session_state:
    st.session_state.use_sample = False

u1, u2 = st.columns(2)
with u1:
    with st.container(border=True):
        st.markdown("##### 🟢 Jobber export")
        jobber_file = st.file_uploader(
            "Jobber export",
            type=["csv", "xlsx"],
            key="jobber",
            label_visibility="collapsed",
        )
with u2:
    with st.container(border=True):
        st.markdown("##### 🔵 QuickBooks export")
        qb_file = st.file_uploader(
            "QuickBooks export",
            type=["csv", "xlsx"],
            key="qb",
            label_visibility="collapsed",
        )

if not (jobber_file and qb_file) and not st.session_state.use_sample:
    s1, s2, s3 = st.columns([1, 1.2, 1])
    with s2:
        if st.button(
            "✨ No files handy? Try it with sample data", use_container_width=True
        ):
            st.session_state.use_sample = True

jobber_raw = qb_raw = None
if jobber_file and qb_file:
    try:
        jobber_raw, qb_raw = load_table(jobber_file), load_table(qb_file)
        source = f"upload:{jobber_file.name}:{jobber_file.size}:{qb_file.name}:{qb_file.size}"
    except Exception as e:  # noqa: BLE001 — show any parse failure to the user
        st.error(f"Couldn't read one of those files: {e}")
        st.stop()
elif st.session_state.use_sample:
    jobber_raw, qb_raw = load_table("jobber_invoices.csv"), load_table(
        "quickbooks_export.csv"
    )
    source = "sample"
    st.info(
        "📎 Using sample data (60 synthetic invoices). Upload your own files above to check your real books."
    )

if jobber_raw is None:
    st.markdown("")
    c1, c2, c3 = st.columns(3)
    for col, (icon, title, text) in zip(
        (c1, c2, c3),
        [
            (
                "📤",
                "Upload",
                "Export invoices from Jobber and transactions from QuickBooks.",
            ),
            (
                "🔗",
                "Match columns",
                "We guess which column is which. Add any extra columns you want checked.",
            ),
            (
                "✅",
                "Get a fix list",
                "See every mismatch with the dollar impact, then download it as a CSV.",
            ),
        ],
    ):
        with col:
            with st.container(border=True):
                st.markdown(f"### {icon}\n**{title}**\n\n{text}")
    st.stop()

# Widget keys include a fingerprint of the data so switching files resets the mapping.
sig = hashlib.md5(
    (source + "|".join(jobber_raw.columns) + "|".join(qb_raw.columns)).encode()
).hexdigest()[:8]

# ---------------------------------------------------------------- step 2: mapping
step(
    2,
    "Line up your columns",
    "Every business exports a little differently. We pre-filled our best guess, so check it and adjust if needed.",
)


def mapping_ui(df, side_label, icon, prefix):
    guesses = guess_mapping(list(df.columns))
    options = [NOT_IN_FILE] + list(df.columns)
    confirmed = {}
    with st.container(border=True):
        st.markdown(
            f"##### {icon} {side_label} · {len(df):,} rows · {len(df.columns)} columns"
        )
        grid = st.columns(2)
        for i, (field, label) in enumerate(FIELD_LABELS.items()):
            default = guesses.get(field)
            with grid[i % 2]:
                choice = st.selectbox(
                    label,
                    options,
                    index=options.index(default) if default in options else 0,
                    key=f"{prefix}_{field}_{sig}",
                )
            confirmed[field] = None if choice == NOT_IN_FILE else choice
        with st.expander("Preview file"):
            mapped = [c for c in confirmed.values() if c]
            styled = df.head(8).style.set_properties(
                subset=mapped, **{"background-color": "#E6F6EF"}
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)
    return confirmed


m1, m2 = st.columns(2)
with m1:
    jobber_mapping = mapping_ui(jobber_raw, "Jobber", "🟢", "jm")
with m2:
    qb_mapping = mapping_ui(qb_raw, "QuickBooks", "🔵", "qm")

# ---- extra column pairs
st.markdown(
    "#### ➕ Compare more columns <span style='font-size:.85rem;color:#7A8A82;font-weight:400'>optional</span>",
    unsafe_allow_html=True,
)
st.caption(
    "Pick any column from each file to check on every matched invoice, like joining two tables. "
    "We pre-filled pairs that look related. Add a row for anything else you care about "
    "(job description, status, tax, due date…)."
)

suggested = suggest_extra_pairs(
    jobber_raw,
    qb_raw,
    {c for c in jobber_mapping.values() if c},
    {c for c in qb_mapping.values() if c},
)
pairs_seed = pd.DataFrame(
    [
        {
            "Check": True,
            "Jobber column": p["jobber_col"],
            "QuickBooks column": p["qb_col"],
            "Compare as": p["mode"],
        }
        for p in suggested
    ],
    columns=["Check", "Jobber column", "QuickBooks column", "Compare as"],
)
if pairs_seed.empty:
    pairs_seed.loc[0] = [True, None, None, "Text (exact)"]

edited = st.data_editor(
    pairs_seed,
    key=f"extra_{sig}",
    num_rows="dynamic",
    hide_index=True,
    use_container_width=True,
    column_config={
        "Check": st.column_config.CheckboxColumn("On", width="small", default=True),
        "Jobber column": st.column_config.SelectboxColumn(
            "🟢 Jobber column", options=list(jobber_raw.columns)
        ),
        "QuickBooks column": st.column_config.SelectboxColumn(
            "🔵 QuickBooks column", options=list(qb_raw.columns)
        ),
        "Compare as": st.column_config.SelectboxColumn(
            "Compare as",
            options=COMPARE_MODES,
            default="Text (exact)",
            help="Text (exact) ignores case and spacing. Text (fuzzy) tolerates typos. "
            "Number and Date understand formatting like $1,200.00 or 08/02/2026.",
        ),
    },
)
extra_pairs = [
    {
        "label": (
            r["Jobber column"]
            if r["Jobber column"] == r["QuickBooks column"]
            else f"{r['Jobber column']} ↔ {r['QuickBooks column']}"
        ),
        "jobber_col": r["Jobber column"],
        "qb_col": r["QuickBooks column"],
        "mode": r["Compare as"] or "Text (exact)",
    }
    for _, r in edited.iterrows()
    if r["Check"] and pd.notna(r["Jobber column"]) and pd.notna(r["QuickBooks column"])
]

# ---- visual summary of how the two files line up
rows_html = "".join(
    f'<div class="link-row">{chip(jobber_mapping[f], "j")}'
    f'<div class="connector">{"⟷" if jobber_mapping[f] and qb_mapping[f] else "·"}</div>'
    f'{chip(qb_mapping[f], "q")}</div>'
    for f in FIELD_LABELS
) + "".join(
    f'<div class="link-row">{chip(p["jobber_col"], "j", True)}<div class="connector extra">⟷</div>'
    f'{chip(p["qb_col"], "q", True)}</div>'
    for p in extra_pairs
)
st.markdown(
    f'<div class="links"><div class="head"><div>🟢 Jobber</div><div></div>'
    f'<div style="text-align:right">QuickBooks 🔵</div></div>{rows_html}</div>',
    unsafe_allow_html=True,
)

with st.expander("⚙️ Matching rules"):
    r1, r2 = st.columns(2)
    amount_tol = r1.number_input(
        "Ignore amount differences under ($)",
        min_value=0.0,
        value=1.0,
        step=0.5,
        help="Rounding pennies aren't worth flagging. Set to 0 to catch everything.",
    )
    date_tol = r2.slider(
        "Allow dates to differ by up to (days)",
        0,
        7,
        0,
        help="Some syncs post a day late. Raise this to stop flagging those.",
    )

missing = [
    FIELD_LABELS[f] for f in FIELD_LABELS if not (jobber_mapping[f] and qb_mapping[f])
]
if missing:
    st.warning(f"Pick a column on both sides for: **{', '.join(missing)}**")

st.markdown("")
run = st.button(
    f"🔍 Find the mismatches ({len(FIELD_LABELS) + len(extra_pairs)} column pairs)",
    type="primary",
    use_container_width=True,
    disabled=bool(missing),
)

if run:
    try:
        jobber_df = apply_mapping(jobber_raw, jobber_mapping, SIDE_A_RENAME_TO)
        qb_df = apply_mapping(qb_raw, qb_mapping, SIDE_B_RENAME_TO)
    except ValueError as e:
        st.error(str(e))
        st.stop()
    # Copy extra columns under private names so core renames can't collide with them.
    engine_pairs = []
    for i, p in enumerate(extra_pairs):
        jobber_df[f"_x{i}"] = jobber_raw[p["jobber_col"]]
        qb_df[f"_x{i}"] = qb_raw[p["qb_col"]]
        engine_pairs.append({**p, "jobber_col": f"_x{i}", "qb_col": f"_x{i}"})
    with st.spinner("Cross-checking every invoice…"):
        st.session_state.result = {
            "sig": sig,
            "data": reconcile(jobber_df, qb_df, engine_pairs, amount_tol, date_tol),
        }

result = st.session_state.get("result")
if not result or result["sig"] != sig:
    st.stop()

# ---------------------------------------------------------------- step 3: results
summary = result["data"]["summary"]
issues = result["data"]["issues"]
total = summary["total_jobber_invoices"]
clean = summary["clean_matches"]
health = round(100 * clean / total) if total else 100
ring_color = "#0E9F6E" if health >= 85 else "#FFB224" if health >= 60 else "#E5484D"

step(
    3,
    "Here's what we found",
    f"{total:,} Jobber invoices checked against {summary['total_qb_transactions']:,} "
    "QuickBooks transactions.",
)

h, k1, k2, k3 = st.columns([1.5, 1, 1, 1])
h.markdown(
    f'<div class="health"><div class="ring" style="background:conic-gradient({ring_color} {health * 3.6}deg,'
    f' #E8EEEB 0)"><div class="inner"><div class="pct">{health}%</div><div class="cap">in sync</div></div></div>'
    f'<div><b>Books health</b><br><span style="color:#5B6B63;font-size:.9rem">{clean} of {total} invoices match '
    f"cleanly across every column you checked.</span></div></div>",
    unsafe_allow_html=True,
)
k1.markdown(
    kpi("Issues found", f"{len(issues):,}", "across all checks", alert=len(issues) > 0),
    unsafe_allow_html=True,
)
k2.markdown(
    kpi(
        "Dollars at risk",
        f"${summary['likely_dollar_impact']:,.0f}",
        "duplicates + amount gaps",
        alert=summary["likely_dollar_impact"] > 0,
    ),
    unsafe_allow_html=True,
)
k3.markdown(
    kpi(
        "Clients affected",
        f"{issues['client'].nunique():,}" if len(issues) else "0",
        "need a follow-up",
    ),
    unsafe_allow_html=True,
)

if issues.empty:
    st.balloons()
    st.success(
        "🎉 Everything lines up. Your Jobber and QuickBooks records are in sync."
    )
    st.stop()

st.markdown("")
counts = summary["issues_by_type"]
left, right = st.columns([1, 1.3])
with left:
    for t, n in counts.items():
        icon, label, color, why = ISSUE_META.get(t, ("•", t, "#888", ""))
        st.markdown(
            f'<div class="issue-card" style="--c:{color}"><span class="n">{n}</span>'
            f'<div class="t">{icon} {label}</div><div class="d">{why}</div></div>',
            unsafe_allow_html=True,
        )
with right:
    chart_df = pd.DataFrame(
        [
            {"Issue": ISSUE_META.get(t, ("", t))[1], "Count": n}
            for t, n in counts.items()
        ]
    )
    domain = [ISSUE_META[t][1] for t in counts if t in ISSUE_META]
    colors = [ISSUE_META[t][2] for t in counts if t in ISSUE_META]
    donut = (
        alt.Chart(chart_df)
        .mark_arc(innerRadius=70, cornerRadius=4, padAngle=0.015)
        .encode(
            theta="Count:Q",
            color=alt.Color(
                "Issue:N",
                scale=alt.Scale(domain=domain, range=colors),
                legend=alt.Legend(orient="right", title=None, labelFontSize=12),
            ),
            tooltip=["Issue", "Count"],
        )
        .properties(height=260, title="Issue breakdown")
    )
    st.altair_chart(donut, use_container_width=True)

    top_clients = issues.groupby("client").size().nlargest(6).reset_index(name="Issues")
    bars = (
        alt.Chart(top_clients)
        .mark_bar(cornerRadiusEnd=4, color="#0E9F6E")
        .encode(
            x=alt.X("Issues:Q", axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("client:N", sort="-x", title=None),
            tooltip=["client", "Issues"],
        )
        .properties(height=200, title="Clients with the most issues")
    )
    st.altair_chart(bars, use_container_width=True)

# ---- detail tables
st.markdown("#### 📋 The fix list")
table_cfg = {
    "type": st.column_config.TextColumn("Type"),
    "invoice": st.column_config.TextColumn("Invoice"),
    "client": st.column_config.TextColumn("Client"),
    "jobber_amount": st.column_config.NumberColumn("Jobber $", format="$%.2f"),
    "qb_amount": st.column_config.NumberColumn("QuickBooks $", format="$%.2f"),
    "detail": st.column_config.TextColumn("What's wrong", width="large"),
}
display = issues.assign(
    type=issues["type"].map(lambda t: " ".join(ISSUE_META.get(t, ("", t))[:2]))
)

search = st.text_input(
    "Search",
    placeholder="🔎 Filter by invoice #, client or detail…",
    label_visibility="collapsed",
)
if search:
    mask = display.apply(
        lambda r: r.astype(str).str.contains(search, case=False, regex=False).any(),
        axis=1,
    )
    display = display[mask]

tabs = st.tabs(
    [f"All ({len(display)})"]
    + [
        f"{ISSUE_META.get(t, ('', t))[0]} {ISSUE_META.get(t, ('', t))[1]} ({(issues.loc[display.index, 'type'] == t).sum()})"
        for t in counts
    ]
)
with tabs[0]:
    st.dataframe(
        display, use_container_width=True, hide_index=True, column_config=table_cfg
    )
for tab, t in zip(tabs[1:], counts):
    with tab:
        st.dataframe(
            display[issues.loc[display.index, "type"] == t].drop(columns="type"),
            use_container_width=True,
            hide_index=True,
            column_config=table_cfg,
        )

st.download_button(
    "⬇️ Download the full fix list (CSV)",
    issues.to_csv(index=False),
    file_name="books_match_report.csv",
    mime="text/csv",
    use_container_width=True,
)

st.markdown(
    """
<div class="cta">
  <b>Did this catch something real in your books?</b><br>
  This is an early version built around the exact sync problems Jobber + QuickBooks users keep reporting.
  Coming next: saved column mappings, Clio and Bonsai support, weekly auto-checks, and one-click fixes.
  Tell us what you'd want first, because we're building it with the people who use it.
</div>
""",
    unsafe_allow_html=True,
)
if CONTACT_LINK:
    st.link_button(
        "💛 If this caught something real, chip in",
        CONTACT_LINK,
        use_container_width=True,
    )
