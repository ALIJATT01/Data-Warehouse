#!/usr/bin/env python3
"""Data Warehouse Week 6 — Python-track lab pipeline (PakFreight).

Builds, from the raw March 2026 source files only (no hand edits):
  FactShipment_March.csv, RejectLog.csv, DimCustomer.csv, DimDate.csv

Pipeline steps (printed by name as they run):
  STEP 1  EXTRACT           raw text, no correction
  STEP 2  SHAPE_AND_RENAME  contract columns
  STEP 3  REJECT            4 classes -> RejectLog.csv
  STEP 4  DEDUPLICATE       TRIM+UPPER ShipmentID, first occurrence kept
  STEP 5  TRANSFORM         rail tonnes x1000, IDs, dates, status, cities
  STEP 6  ROUTE             city/route/mode/status keys (-1 = unresolved)
  STEP 7  CONFORM_CUSTOMERS surviving business keys (March only)
  STEP 8  BUILD_DIMDATE     gapless Q1 2026 (March) / shipped Feb file (Feb)
  STEP 9  LOAD              fact + all CSVs
  STEP 10 PRINT ladder + T7 acceptance checks (failure exits nonzero)

Parsing rules (exactly the verified profiling rules — do not reinvent):
  * STRICT plain-decimal numbers: ^-?\\d+(\\.\\d+)?$ after strip. In
    particular '1.2e4' is TEXT, not 12000; 'approx 7000', 'see note',
    'TBC', '--', 'unknown', 'n/a' are text too. pandas is read with
    dtype=str and keep_default_na=False so 'n/a' is never swallowed
    into NaN and no value is ever silently converted.
  * Dates parse as DD/MM/YYYY or YYYY-MM-DD only. Anything else
    (00/00/0000, 2026/13/04, 32/03/2026, 31/02/2026, Mar-2026) is
    unparseable and rejected.
  * Cities: alias map exact-match AFTER strip (no case folding).
  * Status: strip + UPPER, then DELIVERED / IN TRANSIT / DELAYED.
    Anything else routes to StatusKey -1 (it is NOT rejected).
  * Customers (March): conform by stripping corporate suffix tokens
    {pvt, pvtltd, private, limited, ltd, company, co} from lowercased
    alphanumeric tokens. Survivor of each business = lowest CU number;
    losers remap to it (CU-901->101, CU-902->103, CU-903->104,
    CU-904->109, CU-905->119, CU-906->131).
  * Rail: weights arrive in TONNES and are multiplied by 1000 in
    STEP 5, before the fact table is built; bare consignment numbers
    become RL-##### (Week 5 convention, e.g. bare 45012 -> RL-45012).

FEBRUARY GENERALITY MODE (--feb):
  The February files have NO CustomerID column at all, so every Feb
  fact row gets CustomerKey -1 (documented, not a data error), and
  Feb DimDate is the shipped February file copied as-is (Q1 is NOT
  rebuilt). Feb acceptance: 35 staged (marine 12 + rail 10 + road 13
  data rows; the road file also carries 1 blank line + 1 TOTAL footer
  as extract artifacts), 0 rejects, 1 duplicate removed, 34 loaded,
  578,650 kg. Feb mode writes no DimCustomer (no customer source
  exists for February) and asserts the Feb checks instead of the
  March T7 checks.

March acceptance (T7, asserted in STEP 10):
  staging 1284; rejected 24; duplicates removed 60; loaded 1200;
  SUM WeightKG 17,542,050; SUM FreightCostPKR 147,037,000;
  OriginCityKey -1 = 14 rows; StatusKey -1 = 7 rows;
  DimCustomer excluding Unknown = 40; DimDate = 90 rows.

Usage (March, defaults):
  python3 W6_su92-bsdsm-f23-018.py
Usage (February generality check):
  python3 W6_su92-bsdsm-f23-018.py --feb \
    --data-dir ~/workspace/data-warehouse/week5/solution/data/source \
    --alias ~/workspace/data-warehouse/week5/solution/data/alias_lookup.csv \
    --dims ~/workspace/data-warehouse/week5/solution/data/dims \
    --out-dir ~/workspace/data-warehouse/week6/solution/feb_check
"""

import argparse
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pandas as pd

HOME = Path.home()
MARCH_DATA = HOME / "workspace/data-warehouse/week6/assignments/data/march"
MARCH_BASE = HOME / "workspace/data-warehouse/week6/assignments/data"
MARCH_OUT = HOME / "workspace/data-warehouse/week6/solution"
FEB_DATA = HOME / "workspace/data-warehouse/week5/solution/data/source"
FEB_BASE = HOME / "workspace/data-warehouse/week5/solution/data"
FEB_OUT = MARCH_OUT / "feb_check"

CONTRACT = ["ShipmentID", "ShipDate", "Origin", "Destination",
            "WeightKG", "FreightCostPKR", "StatusRaw", "CustomerID"]

PLAIN = re.compile(r"^-?\d+(\.\d+)?$")
STATUS_MAP = {"DELIVERED": ("Delivered", 1),
              "IN TRANSIT": ("In Transit", 2),
              "DELAYED": ("Delayed", 3)}
SUFFIX_TOKENS = {"pvt", "pvtltd", "private", "limited", "ltd", "company", "co"}
HOLIDAYS_Q1 = {date(2026, 2, 5), date(2026, 3, 23)}
MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]


def step(n, name):
    print(f"\n=== STEP {n} {name} ===", flush=True)


def norm_id(s):
    return (s or "").strip().upper()


def plain_number(s):
    """Strict plain-decimal parse; None if not a plain decimal string."""
    t = str(s).strip()
    if not PLAIN.match(t):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_date(s):
    """DD/MM/YYYY or YYYY-MM-DD only; None if unparseable."""
    t = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def bizkey(name):
    toks = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    toks = [t for t in toks if t not in SUFFIX_TOKENS]
    return "".join(toks)


def cell_text(v):
    """Raw cell value as arrived, as text (rail Excel cells)."""
    if v is None:
        return ""
    if hasattr(v, "strftime"):  # datetime/date cell -> ISO text
        return v.strftime("%Y-%m-%d")
    return str(v)


# ---------------------------------------------------------------- extract
def extract_csv(path, source_file):
    """Read a CSV as raw text. Returns (rows, artifacts).

    rows: list of dicts with the 8 contract fields (CustomerID '' if
    the file has no customer column) + SourceFile/SourceLine/SourceRow.
    artifacts: (SourceFile, SourceLine, kind) for blank lines and TOTAL
    footers, which are NOT rows. SourceLine is the CSV line number
    with header = 1; skip_blank_lines=False keeps line numbers true.
    """
    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                     skip_blank_lines=False, header=0)
    rows, artifacts = [], []
    for pos in range(len(df)):
        line = pos + 2  # header is line 1
        vals = [df.iat[pos, c] for c in range(df.shape[1])]
        if all((v or "").strip() == "" for v in vals):
            artifacts.append((source_file, line, "blank line"))
            continue
        if (vals[0] or "").strip().upper() == "TOTAL":
            artifacts.append((source_file, line, "TOTAL footer"))
            continue
        rec = dict(zip(CONTRACT, (vals + [""] * 8)[:8]))
        rec["SourceFile"] = source_file
        rec["SourceLine"] = line
        rec["SourceRow"] = "|".join(vals)
        rows.append(rec)
    return rows, artifacts


def extract_rail(path, source_file):
    """Read the rail workbook as raw text. Header row is found by
    searching the first rows for 'consignment'; data follows it.
    Rail Excel row numbers are the SourceLine values."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    # The header row is the one whose first cell IS the consignment-number
    # column name ('consignment_no' / 'Consignment No'). Title rows must
    # NOT match: row 1 is '... CONSIGNMENT REGISTER ...', which contains
    # the word 'consignment' but is not the header.
    header_row = None
    for r in range(1, 6):
        v = ws.cell(row=r, column=1).value
        if isinstance(v, str) and re.sub(r"[^a-z]", "", v.lower()) == "consignmentno":
            header_row = r
            break
    if header_row is None:
        raise SystemExit(f"rail header row not found in {path}")
    rows = []
    for r in range(header_row + 1, ws.max_row + 1):
        vals = [cell_text(ws.cell(row=r, column=c).value) for c in range(1, 9)]
        if all(v.strip() == "" for v in vals):
            continue  # blank Excel row is not a shipment row
        rec = dict(zip(CONTRACT, (vals + [""] * 8)[:8]))
        rec["SourceFile"] = source_file
        rec["SourceLine"] = r
        rec["SourceRow"] = "|".join(vals)
        rows.append(rec)
    return rows, []


def find_source_files(data_dir):
    data_dir = Path(data_dir)
    marine = sorted(data_dir.glob("*marine*.csv"))
    rail = sorted(data_dir.glob("*rail*.xlsx"))
    road = sorted(data_dir.glob("*road*.csv"))
    if not (marine and rail and road):
        raise SystemExit(f"expected marine/rail/road files under {data_dir}, "
                         f"found marine={marine} rail={rail} road={road}")
    return marine[0], rail[0], road[0]


def mode_key(source_file):
    s = source_file.lower()
    if "marine" in s:
        return 1
    if "rail" in s:
        return 2
    if "road" in s:
        return 3
    raise SystemExit(f"cannot infer ModeKey for {source_file}")


def build_dimdate_q1():
    """Gapless 2026-01-01..2026-03-31, same columns as shipped DimDate."""
    out = []
    d = date(2026, 1, 1)
    while d <= date(2026, 3, 31):
        dow = (d.weekday() + 1) % 7 + 1  # 1 = Sunday
        out.append({
            "DateKey": int(d.strftime("%Y%m%d")),
            "FullDate": d.isoformat(),
            "Year": d.year,
            "Quarter": (d.month - 1) // 3 + 1,
            "MonthNumber": d.month,
            "MonthName": MONTH_NAMES[d.month - 1],
            "DayOfMonth": d.day,
            "DayOfWeek": dow,
            "DayName": DAY_NAMES[d.weekday()],
            "IsWeekend": "Yes" if dow in (1, 7) else "No",
            "IsHoliday": "Yes" if d in HOLIDAYS_Q1 else "No",
        })
        d += timedelta(days=1)
    return pd.DataFrame(out, columns=["DateKey", "FullDate", "Year", "Quarter",
                                      "MonthNumber", "MonthName", "DayOfMonth",
                                      "DayOfWeek", "DayName", "IsWeekend",
                                      "IsHoliday"])


def main():
    ap = argparse.ArgumentParser(description="DW Week 6 Python-track pipeline")
    ap.add_argument("--feb", action="store_true",
                    help="February generality mode (no CustomerID column)")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--alias", default=None)
    ap.add_argument("--dims", default=None)
    ap.add_argument("--customers", default=None)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    if args.feb:
        data_dir = Path(args.data_dir) if args.data_dir else FEB_DATA
        alias_path = Path(args.alias) if args.alias else FEB_BASE / "alias_lookup.csv"
        dims_dir = Path(args.dims) if args.dims else FEB_BASE / "dims"
        customers_path = Path(args.customers) if args.customers else None
        out_dir = Path(args.out_dir) if args.out_dir else FEB_OUT
    else:
        data_dir = Path(args.data_dir) if args.data_dir else MARCH_DATA
        alias_path = Path(args.alias) if args.alias else MARCH_BASE / "alias_lookup.csv"
        dims_dir = Path(args.dims) if args.dims else MARCH_BASE / "dims"
        customers_path = (Path(args.customers) if args.customers
                          else MARCH_BASE / "customers_master.csv")
        out_dir = Path(args.out_dir) if args.out_dir else MARCH_OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- STEP 1
    step(1, "EXTRACT")
    marine_path, rail_path, road_path = find_source_files(data_dir)
    staging, artifacts = [], []
    for path, kind in ((marine_path, "csv"), (rail_path, "xlsx"), (road_path, "csv")):
        if kind == "csv":
            rows, arts = extract_csv(path, path.name)
        else:
            rows, arts = extract_rail(path, path.name)
        staging.extend(rows)
        artifacts.extend(arts)
        print(f"  {path.name}: {len(rows)} rows")
    for a in artifacts:
        print(f"  extract artifact (NOT a row): {a[0]} line {a[1]} — {a[2]}")
    df = pd.DataFrame(staging)
    per_file = Counter(df["SourceFile"])
    print(f"  staging total: {len(df)}")
    print(f"  per-file staging rows: {dict(per_file)}")

    # as-extracted sums (SUM skips text; rail still in tonnes here)
    print("  as-extracted sums (text skipped; rail in TONNES — see caveat):")
    for f in (marine_path.name, rail_path.name, road_path.name):
        sub = df[df["SourceFile"] == f]
        w = sum(plain_number(v) for v in sub["WeightKG"]
                if plain_number(v) is not None)
        c = sum(plain_number(v) for v in sub["FreightCostPKR"]
                if plain_number(v) is not None)
        unit = "tonnes" if "rail" in f.lower() else "kg"
        extra = f" (= {w * 1000:,.0f} kg)" if "rail" in f.lower() else ""
        print(f"    {f}: rows={len(sub)} weight={w:,.2f} {unit}{extra} "
              f"cost={c:,.0f} PKR")

    # ---------------------------------------------------------- STEP 2
    step(2, "SHAPE_AND_RENAME")
    # Extract already shaped rows positionally into the contract columns:
    # ShipmentID, ShipDate, Origin, Destination, WeightKG, FreightCostPKR,
    # StatusRaw, CustomerID ('' where the source has no customer column).
    df = df[CONTRACT + ["SourceFile", "SourceLine", "SourceRow"]].copy()
    print(f"  contract columns: {CONTRACT}")
    print(f"  rows after shaping: {len(df)} (unchanged — no correction)")

    # lookups (loaded once, used from STEP 5 on)
    alias = {}
    with open(alias_path, newline="", encoding="utf-8-sig") as fh:
        for r in pd.read_csv(alias_path, dtype=str,
                             keep_default_na=False).to_dict("records"):
            alias[r["RawSpelling"].strip()] = int(r["CityKey"])
    route = {}
    route_df = pd.read_csv(dims_dir / "DimRoute.csv", dtype=str,
                           keep_default_na=False)
    for r in route_df.to_dict("records"):
        route[(int(r["OriginCityKey"]), int(r["DestCityKey"]))] = int(r["RouteKey"])
    print(f"  alias spellings loaded: {len(alias)}; routes loaded: {len(route)}")

    # ---------------------------------------------------------- STEP 3
    step(3, "REJECT")
    wtext = df["WeightKG"].map(
        lambda v: plain_number(v) is None and (v or "").strip() != "")
    baddate = df["ShipDate"].map(lambda v: parse_date(v) is None)
    wneg = df["WeightKG"].map(
        lambda v: (plain_number(v) is not None and plain_number(v) < 0))
    blankid = df["ShipmentID"].map(lambda v: norm_id(v) == "")
    classes = {"weight-text": wtext, "date-unparseable": baddate,
               "weight-negative": wneg, "id-blank": blankid}
    reasons = {
        "weight-text": "WeightKG holds text — not a plain decimal",
        "date-unparseable": "ShipDate unparseable — not DD/MM/YYYY or YYYY-MM-DD",
        "weight-negative": "WeightKG negative — below zero",
        "id-blank": "ShipmentID blank — missing ID",
    }
    # classes must be disjoint (verified for March: 9+6+5+4 = 24)
    idx_sets = {k: set(df.index[m]) for k, m in classes.items()}
    overlap = set()
    keys = list(idx_sets)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            overlap |= idx_sets[keys[i]] & idx_sets[keys[j]]
    print(f"  class counts: { {k: int(m.sum()) for k, m in classes.items()} }")
    print(f"  class overlap rows: {len(overlap)} "
          f"({'disjoint — counts add to the union' if not overlap else 'OVERLAP: first-match priority used'})")
    rejected = df[wtext | baddate | wneg | blankid].copy()
    reject_reason = {}
    for idx in rejected.index:  # priority order = class order above
        for k, m in classes.items():
            if m.loc[idx]:
                reject_reason[idx] = reasons[k]
                break
    rejected["RejectReason"] = [reject_reason[i] for i in rejected.index]
    reject_log = pd.DataFrame({
        "RejectID": [f"R-{i + 1:03d}" for i in range(len(rejected))],
        "SourceFile": rejected["SourceFile"].tolist(),
        "SourceLine": rejected["SourceLine"].tolist(),
        "ShipmentID_Raw": rejected["ShipmentID"].tolist(),
        "RejectReason": rejected["RejectReason"].tolist(),
        "SourceRow": rejected["SourceRow"].tolist(),
    })
    print(f"  rejected rows (union): {len(rejected)}")
    for r in reject_log.to_dict("records"):
        print(f"    {r['RejectID']} {r['SourceFile']} line {r['SourceLine']} "
              f"id={r['ShipmentID_Raw']!r} — {r['RejectReason']}")
    loadable = df[~(wtext | baddate | wneg | blankid)].copy()
    print(f"  loadable rows: {len(loadable)}")

    # ---------------------------------------------------------- STEP 4
    step(4, "DEDUPLICATE")
    loadable["_norm_id"] = loadable["ShipmentID"].map(norm_id)
    before = len(loadable)
    deduped = loadable.drop_duplicates(subset=["_norm_id"], keep="first").copy()
    removed = before - len(deduped)
    # breakdown: byte-identical second copies vs trailing-space-only copies
    raw_cols = CONTRACT
    seen_raw, byte_removed = set(), 0
    for _, row in loadable.iterrows():
        k = tuple(row[c] for c in raw_cols)
        if k in seen_raw:
            byte_removed += 1
        else:
            seen_raw.add(k)
    print(f"  key: TRIM+UPPER ShipmentID, first occurrence kept")
    print(f"  removed: {removed} ({byte_removed} byte-identical second copies "
          f"+ {removed - byte_removed} trailing-space-only copies)")
    print(f"  rows after dedup: {len(deduped)}")
    deduped = deduped.drop(columns=["_norm_id"])

    # ---------------------------------------------------------- STEP 5
    step(5, "TRANSFORM")
    work = deduped.copy()
    is_rail = work["SourceFile"].map(lambda f: "rail" in f.lower())
    # rail tonnes -> kg BEFORE the fact table is built (rail rows only)
    wnum = work["WeightKG"].map(plain_number)
    wkg = wnum.copy()
    wkg[is_rail] = wkg[is_rail] * 1000
    work["WeightKG_num"] = wkg.map(lambda v: int(round(v)))
    work["FreightCostPKR_num"] = work["FreightCostPKR"].map(
        lambda v: int(round(plain_number(v))))
    # IDs: TRIM+UPPER; rail bare numbers restored to RL-#####
    def transform_id(row):
        nid = norm_id(row["ShipmentID"])
        if "rail" in row["SourceFile"].lower() and not nid.startswith("RL-"):
            return "RL-" + nid
        return nid
    work["ShipmentID"] = work.apply(transform_id, axis=1)
    work["ShipDate_parsed"] = work["ShipDate"].map(parse_date)
    work["DateKey"] = work["ShipDate_parsed"].map(
        lambda d: int(d.strftime("%Y%m%d")))
    # status: TRIM + title-case via the status map (unresolved kept raw)
    work["StatusKey"] = work["StatusRaw"].map(
        lambda v: STATUS_MAP.get((v or "").strip().upper(), ("", -1))[1])
    # cities via alias map, exact match after strip
    work["OriginCityKey"] = work["Origin"].map(
        lambda v: alias.get((v or "").strip(), -1))
    work["DestCityKey"] = work["Destination"].map(
        lambda v: alias.get((v or "").strip(), -1))
    n_unk_origin = int((work["OriginCityKey"] == -1).sum())
    n_unk_status = int((work["StatusKey"] == -1).sum())
    print(f"  rail tonnes x1000 applied to {int(is_rail.sum())} rail rows")
    print(f"  credit-note rows kept as negative costs: "
          f"{int((work['FreightCostPKR_num'] < 0).sum())}")
    print(f"  unresolved origins (-> -1): {n_unk_origin}; "
          f"unresolved statuses (-> -1): {n_unk_status}; "
          f"unresolved destinations: {int((work['DestCityKey'] == -1).sum())}")

    # ---------------------------------------------------------- STEP 6
    step(6, "ROUTE")
    work["RouteKey"] = [route.get((o, d), -1) for o, d in
                        zip(work["OriginCityKey"], work["DestCityKey"])]
    work["ModeKey"] = work["SourceFile"].map(mode_key)
    print(f"  RouteKey -1 rows: {int((work['RouteKey'] == -1).sum())} "
          f"(= unresolved-origin rows; all destinations resolve)")
    print(f"  ModeKey: marine=1 rail=2 road=3 -> "
          f"{dict(Counter(work['ModeKey']))}")

    # ---------------------------------------------------------- STEP 7
    step(7, "CONFORM_CUSTOMERS")
    if args.feb or customers_path is None:
        work["CustomerKey"] = -1
        dim_customer = None
        print("  FEBRUARY MODE: source files carry no CustomerID column, so "
              "CustomerKey = -1 for every fact row (documented, not an error).")
        print("  DimCustomer is not built in Feb mode (no customer source).")
    else:
        cust = pd.read_csv(customers_path, dtype=str, keep_default_na=False)
        cust["num"] = cust["CustomerID"].map(lambda v: int(v.split("-")[1]))
        cust["biz"] = cust["CustomerName"].map(bizkey)
        groups = {}
        for _, r in cust.iterrows():
            groups.setdefault(r["biz"], []).append(r)
        survivor_of, alias_map, dim_rows = {}, {}, []
        for biz, members in groups.items():
            members = sorted(members, key=lambda r: r["num"])
            surv = members[0]
            survivor_of[biz] = surv
            losers = [m["CustomerName"] for m in members[1:]]
            for m in members[1:]:
                alias_map[m["num"]] = surv["num"]
            dim_rows.append({
                "CustomerKey": int(surv["num"]),
                "CustomerName": surv["CustomerName"],
                "City": surv["City"],
                "Sector": surv["Sector"],
                "Aliases": "; ".join(losers),
            })
        print(f"  registry rows: {len(cust)} -> businesses: {len(dim_rows)}")
        print(f"  duplicate pairs remapped: "
              f"{ {f'CU-{k}': f'CU-{v}' for k, v in sorted(alias_map.items())} }")
        work["CustomerKey"] = work["CustomerID"].map(
            lambda v: alias_map.get(int(v.split("-")[1]),
                                    int(v.split("-")[1])))
        dim_customer = pd.DataFrame(dim_rows).sort_values("CustomerKey")
        dim_customer = pd.concat([dim_customer, pd.DataFrame([{
            "CustomerKey": -1, "CustomerName": "Unknown",
            "City": "Unknown", "Sector": "Unknown", "Aliases": "",
        }])], ignore_index=True)
        print(f"  DimCustomer rows incl. Unknown: {len(dim_customer)}")

    # ---------------------------------------------------------- STEP 8
    step(8, "BUILD_DIMDATE")
    if args.feb:
        dim_date = pd.read_csv(dims_dir / "DimDate.csv", dtype=str,
                               keep_default_na=False)
        # keep numeric columns numeric for a clean write-back
        for c in ["DateKey", "Year", "Quarter", "MonthNumber",
                  "DayOfMonth", "DayOfWeek"]:
            dim_date[c] = dim_date[c].astype(int)
        print(f"  FEBRUARY MODE: DimDate = shipped February file as-is "
              f"({len(dim_date)} rows, NOT rebuilt)")
    else:
        dim_date = build_dimdate_q1()
        print(f"  DimDate rebuilt gapless 2026-01-01..2026-03-31: "
              f"{len(dim_date)} rows (Q1 2026)")
        print("  holidays: 2026-02-05 and 2026-03-23")
        shipped = pd.read_csv(dims_dir / "DimDate.csv", dtype=str,
                              keep_default_na=False)
        feb_gen = dim_date[dim_date["MonthNumber"] == 2].copy()
        for c in ["DateKey", "Year", "Quarter", "MonthNumber",
                  "DayOfMonth", "DayOfWeek"]:
            feb_gen[c] = feb_gen[c].astype(str)
            shipped[c] = shipped[c].astype(str)
        feb_match = feb_gen.reset_index(drop=True).equals(
            shipped.reset_index(drop=True))
        print(f"  cross-check: rebuilt February rows match the shipped "
              f"February DimDate file: {feb_match}")

    # ---------------------------------------------------------- STEP 9
    step(9, "LOAD")
    fact = pd.DataFrame({
        "ShipmentID": work["ShipmentID"],
        "DateKey": work["DateKey"].astype(int),
        "OriginCityKey": work["OriginCityKey"].astype(int),
        "DestCityKey": work["DestCityKey"].astype(int),
        "RouteKey": work["RouteKey"].astype(int),
        "ModeKey": work["ModeKey"].astype(int),
        "StatusKey": work["StatusKey"].astype(int),
        "CustomerKey": work["CustomerKey"].astype(int),
        "WeightKG": work["WeightKG_num"].astype(int),
        "FreightCostPKR": work["FreightCostPKR_num"].astype(int),
        "ShipmentCount": 1,
    })
    fact_name = ("FactShipment_March.csv" if not args.feb
                 else "FactShipment_February.csv")
    fact.to_csv(out_dir / fact_name, index=False)
    reject_log.to_csv(out_dir / "RejectLog.csv", index=False)
    dim_date.to_csv(out_dir / "DimDate.csv", index=False)
    if dim_customer is not None:
        dim_customer.to_csv(out_dir / "DimCustomer.csv", index=False)
    print(f"  wrote {out_dir / fact_name} ({len(fact)} rows)")
    print(f"  wrote {out_dir / 'RejectLog.csv'} ({len(reject_log)} rows)")
    print(f"  wrote {out_dir / 'DimDate.csv'} ({len(dim_date)} rows)")
    if dim_customer is not None:
        print(f"  wrote {out_dir / 'DimCustomer.csv'} ({len(dim_customer)} rows)")

    # ---------------------------------------------------------- STEP 10
    step(10, "PRINT LADDER + ACCEPTANCE CHECKS")
    total_w = int(fact["WeightKG"].sum())
    total_c = int(fact["FreightCostPKR"].sum())
    print("  LADDER")
    print(f"    per-file staging rows: {dict(per_file)}")
    print(f"    staging: {len(df)}")
    print(f"    rejected: {len(rejected)}")
    print(f"    loadable: {len(loadable)}")
    print(f"    duplicates removed: {removed}")
    print(f"    loaded: {len(fact)}")
    print(f"    SUM WeightKG: {total_w:,}")
    print(f"    SUM FreightCostPKR: {total_c:,}")
    print(f"    OriginCityKey -1 rows: {int((fact['OriginCityKey'] == -1).sum())} "
          f"({int(fact.loc[fact['OriginCityKey'] == -1, 'WeightKG'].sum()):,} kg)")
    print(f"    StatusKey -1 rows: {int((fact['StatusKey'] == -1).sum())}")

    print("  T6 SPOT-CHECKS (from this fact table):")
    expected_t6 = {"DateKey": (31, 40), "OriginCityKey": (9, 605),
                   "DestCityKey": (13, 175), "RouteKey": (21, 113),
                   "ModeKey": (3, 660), "StatusKey": (4, 821),
                   "CustomerKey": (40, 57), "ShipmentID": (1200, 1)}
    for col in ["DateKey", "OriginCityKey", "DestCityKey", "RouteKey",
                "ModeKey", "StatusKey", "CustomerKey", "ShipmentID"]:
        cnt = Counter(fact[col])
        worst = cnt.most_common(1)[0][1]
        exp_d, exp_w = expected_t6[col] if not args.feb else (None, None)
        if args.feb:
            print(f"    {col}: distinct={len(cnt)} worst={worst}")
        else:
            flag = ("MATCH" if (len(cnt) == exp_d and worst == exp_w)
                    else "DIFFERS — reported as computed, not fudged")
            print(f"    {col}: distinct={len(cnt)} (expected {exp_d}) "
                  f"worst={worst} (expected {exp_w}) — {flag}")

    print("  ACCEPTANCE CHECKS:")
    checks = []
    if args.feb:
        checks = [
            ("staging rows", len(df), 35),
            ("rejected rows", len(rejected), 0),
            ("duplicates removed", removed, 1),
            ("loaded rows", len(fact), 34),
            ("SUM WeightKG", total_w, 578650),
            ("SUM FreightCostPKR", total_c, 4808000),
            ("DimDate rows (shipped Feb)", len(dim_date), 28),
            ("CustomerKey -1 rows (no customer column)", int((fact["CustomerKey"] == -1).sum()), 34),
        ]
    else:
        checks = [
            ("T7-1 staging rows", len(df), 1284),
            ("T7-2 rejected rows", len(rejected), 24),
            ("T7-3 duplicates removed", removed, 60),
            ("T7-4 loaded rows", len(fact), 1200),
            ("T7-5 SUM WeightKG", total_w, 17542050),
            ("T7-6 SUM FreightCostPKR", total_c, 147037000),
            ("T7-7 OriginCityKey -1 rows", int((fact["OriginCityKey"] == -1).sum()), 14),
            ("T7-8 StatusKey -1 rows", int((fact["StatusKey"] == -1).sum()), 7),
            ("T7-9 DimCustomer excl. Unknown", len(dim_customer) - 1, 40),
            ("T7-10 DimDate rows", len(dim_date), 90),
            ("extra: rebuilt Feb rows match shipped Feb DimDate",
             1 if feb_match else 0, 1),
            ("extra: reject classes disjoint", 0 if overlap else 1, 1),
            ("extra: reject classes sum to union",
             sum(int(m.sum()) for m in classes.values()), len(rejected)),
        ]
    failed = []
    for name, got, exp in checks:
        ok = got == exp
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}: got {got}, expected {exp}")
        if not ok:
            failed.append(name)
    if failed:
        print(f"\nFAILED CHECKS: {failed}")
        sys.exit(1)
    print("\nALL ACCEPTANCE CHECKS PASSED")


if __name__ == "__main__":
    main()
