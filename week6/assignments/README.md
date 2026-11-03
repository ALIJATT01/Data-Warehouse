# Data Warehouse — Week 6: Query Optimization & Data Quality

March 2026 PakFreight quality lab. Python track (no .pbix).

## Contents

- `W6_Quality_su92-bsdsm-f23-018.xlsx` — T1–T7 workbook (T7: 10/10 checks pass)
- `W6_su92-bsdsm-f23-018.py` / `.ipynb` — dual-route pipeline, 10 named steps,
  prints the reconciliation ladder and asserts all 10 T7 checks
- `FactShipment_March.csv` (1,200 rows) — 8 keys + 3 facts, Unknown member −1
- `RejectLog.csv` (24 rows) — every row with file, line and reason
- `DimCustomer.csv` (40 businesses + Unknown), `DimDate.csv` (90 rows, gapless Q1 2026)
- `W6_OnePageNote_su92-bsdsm-f23-018.md` — rejects, Unknown routing, −1 spellings, index decision
- `data/` — March source files (marine / rail / road), dims, customer registry, alias map

## Verified results

- Identity: **1,284 = 1,200 loaded + 24 rejected + 60 duplicates removed**
- Totals: **17,542,050 kg / PKR 147,037,000**
- Unknown city −1 = 14 rows (195,150 kg), Unknown status −1 = 7 rows
- February generality test: 34 rows, 578,650 kg, 0 rejects

## Run

```bash
python3 W6_su92-bsdsm-f23-018.py
```

The script defaults to the workspace layout; against this folder use the flags:

```bash
python3 W6_su92-bsdsm-f23-018.py \
  --data-dir data/march --alias data/alias_lookup.csv --dims data/dims \
  --customers data/customers_master.csv --out-dir .
```
