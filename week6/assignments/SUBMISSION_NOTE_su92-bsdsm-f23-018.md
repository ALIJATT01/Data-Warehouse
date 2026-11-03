# Submission note — Data Warehouse, Week 6

**Course:** Data Warehouse — Week 6, "Query Optimization & Data Quality"
**Case:** PakFreight, March 2026. **Roll No:** su92-bsdsm-f23-018.
**Track:** Python (pandas). No `.pbix` — the brief allows a `.py` instead.

## What is in this folder

| File | What it is |
|---|---|
| `W6_Quality_su92-bsdsm-f23-018.xlsx` | The workbook: T1 carry-forward audit, T2 profile, T3 reject contract, T4 reconciliation, T5 duplicates, T6 index and join path, T7 definition of done |
| `W6_su92-bsdsm-f23-018.py` | The dual-route pipeline: 10 named steps, prints the ladder, asserts all 10 T7 checks |
| `lab_pipeline_su92-bsdsm-f23-018.ipynb` | The same pipeline as an executed notebook, one cell per step |
| `Task1_su92-bsdsm-f23-018.ipynb` … `Task7_su92-bsdsm-f23-018.ipynb` | One executed notebook per task; every number is computed from `data/` in-cell and asserted against the brief's targets |
| `ONE_PAGE_su92-bsdsm-f23-018.md` | One page in my own words: rejects, Unknown routing, −1 spellings, index decision |
| `README_su92-bsdsm-f23-018.md` | How to run everything from this folder |
| `data/` | March source files only (marine / rail / road, dims, customer registry, alias map) |

## How to run

```bash
python3 W6_su92-bsdsm-f23-018.py \
  --data-dir data/march --alias data/alias_lookup.csv --dims data/dims \
  --customers data/customers_master.csv --out-dir .
```

Or open `lab_pipeline_su92-bsdsm-f23-018.ipynb` / any `TaskN.ipynb` and run all cells from this folder.
Running the pipeline writes `FactShipment_March.csv`, `RejectLog.csv`,
`DimCustomer.csv` and `DimDate.csv` next to the script.

## Verification summary (all computed, all asserted in the notebooks)

- Identity: **1,284 extracted = 1,200 loaded + 24 rejected + 60 deduplicated**
- Totals: **17,542,050 kg / PKR 147,037,000** (exact)
- T7: **10/10 checks pass** (1,284; 24; 60; 1,200; 17,542,050; 147,037,000;
  city −1 = 14; status −1 = 7; DimCustomer 40; DimDate 90)
- T1: exactly 3 of 15 claims true; T2: rule-failure counts sum to 105;
  T5: duplicate definitions give 1212 / 1212 / 1200 / 1198;
  T6: star 1,200 probes vs snowflake 3,600 probes
- Nothing is dropped silently: every rejected row is in `RejectLog` with file,
  line and reason; every routed row is counted at key −1
- This folder replaces an earlier upload of the same week that was withdrawn
  before this submission; the structure and filenames here are the final ones
