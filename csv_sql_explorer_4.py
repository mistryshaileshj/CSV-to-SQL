"""
CSV SQL Explorer (DuckDB in-memory)
-----------------------------------
Upload one or more CSV files, register them as tables in an in-memory DuckDB
database, and run arbitrary SQL against them (WHERE, GROUP BY, HAVING, CASE,
window functions, CTEs, JOINs across files, etc.).

Type handling:
  Every file is first read as raw text so nothing is lost. Columns are then
  converted to INTEGER / DOUBLE / TIMESTAMP unless you mark them as TEXT.
  Columns whose values carry leading zeros (e.g. "011") are auto-detected and
  pre-selected as TEXT, since inference would silently turn them into 11.

Run with:
    pip install streamlit duckdb pandas pyarrow
    streamlit run csv_sql_explorer.py
"""

import io
import re
import time
import hashlib

import duckdb
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Read CSV with SQL", page_icon="🦆", layout="wide")


# ----------------------------------------------------------------------------
# One in-memory DuckDB per browser session
# ----------------------------------------------------------------------------
@st.cache_resource
def get_connection():
    return duckdb.connect(database=":memory:")


con = get_connection()

LEADING_ZERO_RE = re.compile(r"^0\d+$")


def sanitize_table_name(filename: str) -> str:
    name = filename.rsplit(".", 1)[0].lower()
    name = re.sub(r"[^0-9a-z_]+", "_", name).strip("_")
    if not name or name[0].isdigit():
        name = f"t_{name}"
    return name


def clean_columns(cols):
    return [
        re.sub(r"[^0-9a-zA-Z_]+", "_", str(c).replace("\ufeff", "")).strip("_").lower()
        or f"col_{i}"
        for i, c in enumerate(cols)
    ]


# ----------------------------------------------------------------------------
# Step 1: read the file as pure text. Nothing is inferred, nothing is lost.
# Cached so this happens once per file, not on every rerun.
# ----------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def read_raw(
    file_bytes: bytes, delimiter: str, has_header: bool, encoding: str, drop_blank_rows: bool
) -> pd.DataFrame:
    df = pd.read_csv(
        io.BytesIO(file_bytes),
        sep=delimiter,
        header=0 if has_header else None,
        encoding=encoding,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        low_memory=False,
    )
    if not has_header:
        df.columns = [f"col_{i}" for i in range(len(df.columns))]
    df.columns = clean_columns(df.columns)

    if drop_blank_rows:
        blank = df.apply(lambda r: all(str(v).strip() == "" for v in r), axis=1)
        df = df[~blank].reset_index(drop=True)
    return df


# ----------------------------------------------------------------------------
# Step 2: profile each column so the UI can pre-select sensible TEXT columns
# and show what each column would become.
# ----------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def profile_columns(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for c in df.columns:
        s = df[c].astype(str).str.strip()
        nonblank = s[s != ""]
        leading_zero = bool(nonblank.head(5000).str.match(LEADING_ZERO_RE).any())

        if nonblank.empty:
            inferred = "TEXT"
        elif pd.to_numeric(nonblank, errors="coerce").notna().all():
            vals = pd.to_numeric(nonblank, errors="coerce")
            inferred = "INTEGER" if (vals % 1 == 0).all() else "DOUBLE"
        else:
            dt = pd.to_datetime(nonblank, errors="coerce", format="mixed")
            inferred = "TIMESTAMP" if dt.notna().all() else "TEXT"

        rows.append({
            "column": c,
            "would_become": inferred,
            "leading_zeros": leading_zero,
            "sample": nonblank.iloc[0] if not nonblank.empty else "",
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Step 3: apply conversions, leaving the user's TEXT columns untouched.
# ----------------------------------------------------------------------------
def apply_types(df: pd.DataFrame, text_cols: set) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if c in text_cols:
            continue
        s = out[c].astype(str).str.strip()
        nonblank = s[s != ""]
        if nonblank.empty:
            continue

        num = pd.to_numeric(nonblank, errors="coerce")
        if num.notna().all():
            full = pd.to_numeric(s.replace("", None), errors="coerce")
            # Int64 is nullable, so blanks stay NULL instead of forcing float.
            out[c] = full.astype("Int64") if (num % 1 == 0).all() else full
            continue

        dt = pd.to_datetime(nonblank, errors="coerce", format="mixed")
        if dt.notna().all():
            out[c] = pd.to_datetime(s.replace("", None), errors="coerce", format="mixed")
    return out


def file_signature(files) -> tuple:
    return tuple(
        (f.name, len(f.getvalue()), hashlib.md5(f.getvalue()).hexdigest()[:8]) for f in files
    )


# ----------------------------------------------------------------------------
# Sidebar: upload + parsing options
# ----------------------------------------------------------------------------
st.sidebar.header("1. Load CSV files")

uploaded_files = st.sidebar.file_uploader(
    "Choose one or more CSV files",
    type=["csv", "txt", "tsv"],
    accept_multiple_files=True,
)

with st.sidebar.expander("Parsing options", expanded=False):
    delimiter = st.text_input("Delimiter", value=",")
    has_header = st.checkbox("First row is header", value=True)
    encoding = st.selectbox(
        "Encoding", ["utf-8-sig", "utf-8", "latin-1", "utf-16", "cp1252"],
        help="utf-8-sig also strips the BOM that Excel writes.",
    )
    drop_blank_rows = st.checkbox("Skip completely blank rows", value=True)

st.sidebar.markdown("---")


# ----------------------------------------------------------------------------
# Read raw text frames + profiles for every uploaded file
# ----------------------------------------------------------------------------
raw_frames, profiles = {}, {}
if uploaded_files:
    with st.spinner("Reading file(s)..."):
        for f in uploaded_files:
            tbl = sanitize_table_name(f.name)
            try:
                raw = read_raw(f.getvalue(), delimiter, has_header, encoding, drop_blank_rows)
                raw_frames[tbl] = raw
                profiles[tbl] = profile_columns(raw)
            except Exception as exc:  # noqa: BLE001
                st.sidebar.error(f"Failed to read **{f.name}**: {exc}")


# ----------------------------------------------------------------------------
# Sidebar: per-column TEXT selection
# ----------------------------------------------------------------------------
text_selection = {}
if raw_frames:
    st.sidebar.header("2. Column types")
    st.sidebar.caption(
        "Everything else is auto-converted to number/date, so you can aggregate "
        "without casting."
    )

    for tbl, prof in profiles.items():
        with st.sidebar.expander(f"{tbl}", expanded=True):
            auto_text = prof.loc[
                prof["leading_zeros"] | (prof["would_become"] == "TEXT"), "column"
            ].tolist()

            key = f"textcols_{tbl}"
            if key not in st.session_state:
                st.session_state[key] = auto_text

            chosen = st.multiselect(
                "Keep as TEXT",
                options=list(prof["column"]),
                key=key,
                help="Pre-selected: columns with leading zeros, plus columns that "
                     "are already text.",
            )
            text_selection[tbl] = set(chosen)

            flagged = prof.loc[prof["leading_zeros"], "column"].tolist()
            if flagged:
                st.caption(f"⚠️ Leading zeros detected in: {', '.join(flagged)}")

            view = prof.copy()
            view["final_type"] = [
                "TEXT" if c in chosen else t
                for c, t in zip(view["column"], view["would_become"])
            ]
            st.dataframe(
                view[["column", "final_type", "sample"]],
                hide_index=True, use_container_width=True,
            )


# ----------------------------------------------------------------------------
# Build DuckDB tables (only when something actually changed)
# ----------------------------------------------------------------------------
def build_tables():
    current = (
        file_signature(uploaded_files), delimiter, has_header, encoding, drop_blank_rows,
        {t: sorted(cols) for t, cols in text_selection.items()},
    )
    if st.session_state.get("load_key") == current:
        return st.session_state.get("tables", {})

    tables = {}
    with st.spinner("Loading into DuckDB..."):
        for tbl, raw in raw_frames.items():
            try:
                typed = apply_types(raw, text_selection.get(tbl, set()))
                con.register(f"_tmp_{tbl}", typed)
                con.execute(f'CREATE OR REPLACE TABLE "{tbl}" AS SELECT * FROM _tmp_{tbl}')
                con.unregister(f"_tmp_{tbl}")
                tables[tbl] = len(typed)
            except Exception as exc:  # noqa: BLE001
                st.sidebar.error(f"Failed to load **{tbl}**: {exc}")
    st.session_state["load_key"] = current
    st.session_state["tables"] = tables
    return tables


tables = build_tables() if raw_frames else st.session_state.get("tables", {})


# ----------------------------------------------------------------------------
# Sidebar: resulting DuckDB schema
# ----------------------------------------------------------------------------
if tables:
    st.sidebar.header("3. Tables in DuckDB")
    for tbl, nrows in tables.items():
        with st.sidebar.expander(f"{tbl}  ({nrows:,} rows)"):
            schema = con.execute(f'PRAGMA table_info("{tbl}")').fetchdf()
            st.dataframe(schema[["name", "type"]], hide_index=True, use_container_width=True)


# ----------------------------------------------------------------------------
# Main pane
# ----------------------------------------------------------------------------
st.title("🦆 Read CSV with SQL")

if not tables:
    st.info("Upload at least one CSV file from the sidebar to get started.")
    st.stop()

first_table = next(iter(tables))
default_sql = f'SELECT *\nFROM "{first_table}"\nLIMIT 100;'

editor_col, run_col = st.columns([5, 1])
with editor_col:
    sql = st.text_area(
        "SQL query",
        value=st.session_state.get("sql_text", default_sql),
        height=200,
        key="sql_text",
    )
with run_col:
    st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    run = st.button("▶ Run query", type="primary", use_container_width=True)


def safe_grid(df: pd.DataFrame, elapsed: float):
    """Serial index starts at 1; object columns stringified for Arrow safety."""
    show = df.copy()
    for col in show.columns:
        if show[col].dtype == object:
            show[col] = show[col].astype(str)
    show.index = range(1, len(show) + 1)
    st.dataframe(show, use_container_width=True, height=320)
    st.markdown(
        f"<div style='text-align:right; color:gray; font-size:0.85em; margin-top:-0.5rem'>"
        f"{len(df):,} rows &times; {len(df.columns)} columns &middot; {elapsed:.3f}s"
        f"</div>",
        unsafe_allow_html=True,
    )


if run:
    statements = [s for s in sql.split(";") if s.strip()]
    if not statements:
        st.warning("Nothing to run.")
        st.stop()

    try:
        with st.spinner("Running query..."):
            start = time.perf_counter()
            result = None
            for stmt in statements:
                result = con.execute(stmt)
            df_out = result.fetchdf()
            elapsed = time.perf_counter() - start

        # st.subheader("Result grid")
        safe_grid(df_out, elapsed)

        st.download_button(
            "⬇ Download full result as CSV",
            df_out.to_csv(index=False).encode("utf-8"),
            file_name="query_result.csv",
            mime="text/csv",
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"**Query failed**\n\n```\n{exc}\n```")
