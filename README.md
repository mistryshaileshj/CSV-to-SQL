# 🦆 Read CSV with SQL

A lightweight [Streamlit](https://streamlit.io/) app for querying CSV files with SQL, powered by an in-memory [DuckDB](https://duckdb.org/) engine. Upload one or more CSVs, run arbitrary SQL against them, and verify the output in an interactive grid — with careful, data-integrity-first type handling that keeps values like account codes (`011`) from being silently corrupted.

Built for data verification and reconciliation workflows, where "the data looked fine" is not good enough.

---

## Why this exists

CSVs lie about their types. A column of account codes like `011`, `0110`, `01100` gets read as numbers by most tools, and the leading zeros vanish — `011` becomes `11`. A single blank row is enough to promote an integer column to float, so you also get `11.0`. On a key column used for joins or lookups, that is not a cosmetic bug; it produces wrong answers that look right.

This tool reads every file as **text first** (lossless), then converts columns to numbers or dates only where it is safe to do so — and lets you override the decision per column.

---

## Features

- **In-memory DuckDB engine** — each uploaded CSV becomes a real, queryable table. No database setup, nothing written to disk.
- **Full SQL support** — `WHERE`, `GROUP BY`, `HAVING`, `CASE`, window functions, CTEs, and `JOIN`s across multiple uploaded files.
- **Text-first, lossless parsing** — files are read as raw text so nothing is corrupted before you have made a decision about it.
- **Per-column type control** — every column is profiled and shown with the type it *would* become (`INTEGER` / `DOUBLE` / `TIMESTAMP` / `TEXT`). Mark any column as `TEXT` to stop inference, so you can aggregate the rest without casting.
- **Leading-zero auto-detection** — columns containing values like `011` are detected automatically and pre-selected as `TEXT`, with a warning.
- **Excel-friendly** — strips the UTF-8 BOM that Excel silently writes (which otherwise corrupts the first column name), and can skip completely blank rows.
- **Null-safe integers** — blank cells become proper `NULL`s using pandas' nullable `Int64`, instead of forcing an otherwise-integer column to float.
- **Schema browser** — inspect column names and resolved types for every loaded table in the sidebar.
- **Interactive result grid** — sortable, with a 1-based row index, row/column counts, and query timing.
- **CSV export** — download the full result of any query.
- **Efficient reruns** — parsing is cached by file-content hash, so editing your SQL does not re-read the file.

---

## Screenshots
<img width="1280" height="588" alt="Screen_1" src="https://github.com/user-attachments/assets/d4894284-c63d-4ff1-81e2-38aa4ff71db2" />
<img width="1277" height="560" alt="Screen_2" src="https://github.com/user-attachments/assets/383021e5-73bc-4820-b195-e994140babe9" />
<img width="1316" height="599" alt="Screen_3" src="https://github.com/user-attachments/assets/8cc49184-9f62-42bf-9d2f-8deda98053c1" />

> Suggested shots: the sidebar column-type panel showing a leading-zero warning, and the result grid after a `GROUP BY` query.

---

## Installation

Requires **Python 3.9+**.

```bash
# clone your repo
git clone https://github.com/<your-username>/read-csv-with-sql.git
cd read-csv-with-sql

# (recommended) create a virtual environment
python -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate

# install dependencies
pip install streamlit duckdb pandas pyarrow
```

Or, if you add the provided `requirements.txt`:

```bash
pip install -r requirements.txt
```

**`requirements.txt`:**

```
streamlit
duckdb
pandas
pyarrow
```

---

## Usage

```bash
streamlit run csv_sql_explorer.py
```

Then, in the browser:

1. **Upload** one or more CSV / TXT / TSV files from the sidebar.
2. **Review column types** under *Column types*. Leading-zero columns are pre-marked as `TEXT`; adjust the selection to taste.
3. **Write SQL** in the editor on the main pane and click **▶ Run query**.
4. **Verify** the result in the grid, and **download** it as CSV if needed.

Each file is registered as a table named after the file. For example, `Ledgers1.csv` becomes the table `ledgers1`. Column names are normalized to be SQL-friendly (lowercased, non-alphanumeric characters replaced with `_`).

---

## How type handling works

The tool processes each file in three stages:

1. **Read as text** — the file is read with every column as a string, so leading zeros, blank cells, and unusual values are preserved exactly.
2. **Profile** — each column is analyzed for what it could become (`INTEGER`, `DOUBLE`, `TIMESTAMP`, or `TEXT`) and whether it contains leading zeros.
3. **Convert** — every column you have *not* marked as `TEXT` is converted to its inferred type, so numeric and date columns are ready to aggregate directly.

Because columns kept as `TEXT` stay as strings, remember to quote literals and cast when you need arithmetic on them:

```sql
-- exact match keeps the leading zero
SELECT * FROM ledgers1 WHERE led_key = '011';

-- prefix search
SELECT * FROM ledgers1 WHERE led_key LIKE '011%';

-- numeric aggregation on a TEXT column; TRY_CAST returns NULL instead of erroring
SELECT cobr_id, SUM(TRY_CAST(amount AS DECIMAL(18,2))) AS total
FROM ledgers1
GROUP BY cobr_id;

-- find values that are NOT valid numbers
SELECT * FROM ledgers1
WHERE TRY_CAST(led_key AS BIGINT) IS NULL AND led_key <> '';
```

Columns left as numbers or dates need no casting at all:

```sql
SELECT cobr_id, COUNT(*) AS n, AVG(ostn_key) AS avg_ostn
FROM ledgers1
GROUP BY cobr_id;
```

---

## Example queries

```sql
-- Filter
SELECT *
FROM ledgers1
WHERE led_name LIKE '%AGARWAL%';

-- Aggregate with HAVING
SELECT cobr_id, COUNT(*) AS ledgers
FROM ledgers1
GROUP BY cobr_id
HAVING COUNT(*) > 100
ORDER BY ledgers DESC;

-- CASE expression
SELECT led_key,
       led_name,
       CASE
           WHEN loc_key IS NULL THEN 'no location'
           ELSE 'located'
       END AS status
FROM ledgers1;

-- Find duplicate keys
SELECT led_key, COUNT(*) AS n
FROM ledgers1
GROUP BY led_key
HAVING COUNT(*) > 1
ORDER BY n DESC;
```

---

## Known limitations

- **Multi-statement splitting is naive.** Statements are split on `;`, which will break if a value contains a semicolon inside a string literal (e.g. `WHERE name = 'a;b'`). _(Fixed in the enhanced-editor version via `sqlparse`.)_
- **Date detection uses `format="mixed"`**, which is flexible but can be slow on very large files with unusual date strings.
- **Everything runs in memory.** File size is bounded by available RAM and by Streamlit's upload limit (200 MB by default).
- **Monetary precision.** Numeric columns are inferred as `INTEGER` or `DOUBLE`. For exact currency arithmetic, keep the column as `TEXT` and `TRY_CAST(... AS DECIMAL(18,2))` in your query. A native per-column `DECIMAL` option is on the roadmap.

---

## Roadmap

Possible future enhancements:

- **Parquet support** alongside CSV (schema-aware, preserves `DECIMAL` and dates natively).
- **Saved / favorite queries** that persist across sessions.
- **Enhanced SQL editor** — syntax highlighting, `Ctrl+Enter` to run, inline error markers, snippets, and query history (via `streamlit-ace` + `sqlparse`).
- **Reconciliation helpers** — generate `EXCEPT` / `FULL OUTER JOIN` / duplicate-key queries between two tables.
- **Save result as a new table** to build on intermediate results.
- **Per-column `DECIMAL` type** for exact monetary sums.
- **Load from a file path** to bypass the upload size limit.
- **Column profiling panel** and **Excel export**.

---

## Contributing

Issues and pull requests are welcome. If you hit a CSV that trips up the type handling, an anonymized sample that reproduces the problem is the most helpful thing you can attach.

---

## License

Released under the MIT License. See [`LICENSE`](LICENSE) for details.

> _Replace this section if you prefer a different license. If you want the tool to be freely reusable, MIT is a common, permissive choice._

---

## Acknowledgments

Built with [Streamlit](https://streamlit.io/), [DuckDB](https://duckdb.org/), and [pandas](https://pandas.pydata.org/).
