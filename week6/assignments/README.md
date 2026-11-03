# Data Warehouse — Week 6: Query Optimization & Data Quality

March 2026 PakFreight quality lab. Python track (no .pbix).

## Contents

- `W6_Quality_su92-bsdsm-f23-018.xlsx` — T1–T7 workbook (T7: 10/10 checks pass)
- `W6_su92-bsdsm-f23-018.py` — dual-route pipeline, 10 named steps,
  prints the reconciliation ladder and asserts all 10 T7 checks
- `lab_pipeline.ipynb` — the same pipeline as an executed notebook (one cell per step)
- `Task1.ipynb` … `Task7.ipynb` — one executed notebook per workbook task;
  every number in them is computed from `data/` in-cell and asserted against the brief
- `FactShipment_March.csv` (1,200 rows), `RejectLog.csv` (24 rows),
  `DimCustomer.csv` (40 businesses + Unknown), `DimDate.csv` (90 rows) —
  produced by running the pipeline (not shipped in this folder)
- `ONE_PAGE.md` — rejects, Unknown routing, −1 spellings, index decision
- `SUBMISSION_NOTE.md` — cover note: contents, how to run, verification summary
- `data/` — March source files (marine / rail / road), dims, customer registry, alias map

## Verified results

- Identity: **1,284 = 1,200 loaded + 24 rejected + 60 duplicates removed**
- Totals: **17,542,050 kg / PKR 147,037,000**
- Unknown city −1 = 14 rows (195,150 kg), Unknown status −1 = 7 rows
- February generality test: 34 rows, 578,650 kg, 0 rejects

## Run

From this folder:

```bash
python3 W6_su92-bsdsm-f23-018.py \
  --data-dir data/march --alias data/alias_lookup.csv --dims data/dims \
  --customers data/customers_master.csv --out-dir .
```

Or open `lab_pipeline.ipynb` (or any `TaskN.ipynb`) and run all cells from this folder.
The pipeline script without flags defaults to the workspace layout; the flags above
point it at this folder's `data/` instead.
