"""PakFreight Week 5 -- The ETL Process, in Python (pandas).

Student: Ali Raza, su92-bsdsm-f23-018, BSDS-7A.
Lab deliverable (Python track): W5_su92-bsdsm-f23-018.py

Rebuilds Week 2's merge as a reusable pipeline: three dirty source files go
in, one clean, keyed FactShipment comes out, and the same functions rerun
unchanged against the unseen night-2 folder.

Usage (from anywhere; paths are relative to this script's own directory):
    python3 W5_su92-bsdsm-f23-018.py            # night 1: the eight checks
    python3 W5_su92-bsdsm-f23-018.py --night2   # refresh test on data/night2/
    python3 W5_su92-bsdsm-f23-018.py --all      # night 1, then the refresh test

Side effects: NONE except prints. The script deliberately writes no CSV:
the .py file itself is the lab deliverable, not a table it happens to emit.
(The fact table it builds is identical to Week 4's FactShipment.)

Requirements: pip install pandas openpyxl
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------- paths
# Relative to this script's own directory, so the script runs from anywhere
# inside the solution folder. Layout: <solution>/W5_....py and <solution>/data/
BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
SOURCE = DATA / "source"          # night 1: the only line changed for refresh
NIGHT2 = DATA / "night2"          # refresh test (same marine + rail, new road)
DIMS = DATA / "dims"

# ---------------------------------------------------------------- contract
# The seven-name contract every source is renamed into BEFORE the append.
# Append before rename would give 20 columns (7 + 7 + 7, only Status shared).
CONTRACT = ["ShipmentID", "ShipDate", "OriginRaw", "DestRaw",
            "WeightKG", "FreightCostPKR", "StatusRaw"]

RENAME = {
    "marine": {"Shipment_ID": "ShipmentID", "Ship_Date": "ShipDate",
               "Origin_Port": "OriginRaw", "Dest_Port": "DestRaw",
               "Weight_KG": "WeightKG", "Cost_PKR": "FreightCostPKR",
               "Status": "StatusRaw"},
    "road": {"trip_id": "ShipmentID", "trip_date": "ShipDate",
             "from_city": "OriginRaw", "to_city": "DestRaw",
             "weight_kg": "WeightKG", "cost_pkr": "FreightCostPKR",
             "status": "StatusRaw"},
    # Rail renames Weight (Tons) to WeightTons, NOT WeightKG: naming it
    # WeightKG while it still holds tons is how a pipeline lies to its
    # maintainer. The tons -> kg conversion is a step of its own, below.
    "rail": {"Consignment No": "ShipmentID", "Dispatch Date": "ShipDate",
             "From Station": "OriginRaw", "To Station": "DestRaw",
             "Weight (Tons)": "WeightTons",
             "Freight Charges (PKR)": "FreightCostPKR", "Status": "StatusRaw"},
}

# ModeKey is assigned per source, before the append -- after the append there
# is nothing left to tell you where a row came from. The values are verified
# against DimMode.csv in load_dimensions(); they are not assumed here.
MODE_NAMES = {"marine": "Marine", "rail": "Rail", "road": "Road"}

# --------------------------------------------------------------------------
# STAGING BOUNDARY -- which frames are INTERMEDIATE and which are PUBLISHED
# (the pandas equivalent of Power Query's "Enable load, off").
#
# INTERMEDIATE (staging / connection-only equivalent -- never published):
#   every per-source frame (Marine_*, Rail_*, Road_*), the appended
#   FactStaging frame, the RemovedDuplicatesOnShipmentID frame, and
#   AliasLookup. They still run on every refresh and still feed the tables
#   below; they just do not appear as model tables.
#
# PUBLISHED (the 6 model tables):
#   the 5 imported dimensions -- DimCity, DimDate, DimMode, DimRoute,
#   DimStatus (imported from data/dims/, NEVER rebuilt) -- plus the one
#   table this pipeline produces: FactShipment.
#
# STEPS is the applied-steps pane for this script: a named list keeping each
# intermediate frame, in run order, so any step can be inspected afterwards,
# e.g. dict(STEPS)["Road_RemovedTotalRow"] or STEPS_BY_NAME["Trimmed..."].
# Step names follow the lab guide's applied steps (RemovedTotalRow,
# TrimmedTextColumns, ...), never FilteredRows1. Debug in the same order the
# pipeline runs: shape, then text, then type, then keys.
# --------------------------------------------------------------------------
STEPS: list = []          # populated by run_pipeline(); see STEPS_BY_NAME
STEPS_BY_NAME: dict = {}  # name -> frame, for step-by-step inspection


def _clean_text(series: pd.Series) -> pd.Series:
    """Text.Clean + Trim equivalent for one text column.

    Control characters are removed, runs of whitespace (tabs, double spaces
    such as 'In  Transit') collapse to a single space, and leading/trailing
    whitespace is stripped. Trim alone does NOT touch interior whitespace.
    """
    return (series.astype("string")
            .str.replace(r"[\x00-\x1f]", "", regex=True)
            .str.replace(r"\s+", " ", regex=True)
            .str.strip())


def load_dimensions() -> dict:
    """Import the five Week 4 dimensions. They are imported, not rebuilt."""
    dims = {}
    for name in ("DimCity", "DimDate", "DimMode", "DimRoute", "DimStatus"):
        dims[name] = pd.read_csv(DIMS / f"{name}.csv")
    # Verify the ModeKey assignment against DimMode.csv -- do not assume it.
    mode_map = dict(zip(dims["DimMode"]["ModeName"], dims["DimMode"]["ModeKey"]))
    # DimMode also carries the Unknown member (-1); verify the three real
    # modes, which are the ones the pipeline assigns.
    assert {k: mode_map[k] for k in ("Marine", "Rail", "Road")} == \
        {"Marine": 1, "Rail": 2, "Road": 3}, mode_map
    return dims


def build_alias_lookup(dim_city: pd.DataFrame) -> pd.DataFrame:
    """Build AliasLookup by splitting DimCity.RawAliases -- do NOT import
    alias_lookup.csv as the lookup. That file is only the check afterwards:
    the built map must match it (23 rows, 14 distinct keys)."""
    alias = (dim_city[["CityKey", "RawAliases"]]
             .assign(RawAliases=lambda d: d.RawAliases.str.split(" | ", regex=False))
             .explode("RawAliases"))
    alias["RawAliases"] = alias["RawAliases"].str.strip()
    alias = alias[alias["RawAliases"].notna() & (alias["RawAliases"] != "")]
    alias = alias.rename(columns={"RawAliases": "RawSpelling"})
    # Check against data/alias_lookup.csv (23 rows, 14 distinct keys).
    check = pd.read_csv(DATA / "alias_lookup.csv")
    assert len(alias) == 23, len(alias)
    assert alias["CityKey"].nunique() == 14, alias["CityKey"].nunique()
    assert len(check) == 23 and check["CityKey"].nunique() == 14
    assert (set(map(tuple, alias[["RawSpelling", "CityKey"]].values))
            == set(map(tuple, check[["RawSpelling", "CityKey"]].values))), \
        "built alias map does not match data/alias_lookup.csv"
    return alias.reset_index(drop=True)


# ------------------------------------------------------------ E: extract
def extract(src: Path, name: str, steps: list) -> pd.DataFrame:
    """Extract corrects nothing: 37 rows land, blank line and TOTAL footer
    included. Any fixing here would leave no untouched copy to prove the
    source against."""
    if name == "rail":
        # Two title rows above the real header. Recorded as two steps, the
        # way the guide's SkippedTitleRows / PromotedHeaders steps read:
        raw_sheet = pd.read_excel(src / "rail_shipments.xlsx",
                                  sheet_name="Rail", header=None)
        steps.append(("Rail_SourceSheet", raw_sheet.copy()))
        df = pd.read_excel(src / "rail_shipments.xlsx",
                           sheet_name="Rail", skiprows=2)
        steps.append(("Rail_SkippedTitleRows", df.copy()))
        steps.append(("Rail_PromotedHeaders", df.copy()))
        return df
    # skip_blank_lines=False so the road file's blank line actually lands
    # and is removed by its own named step, instead of being silently
    # swallowed by the CSV parser before the pipeline ever sees it.
    df = pd.read_csv(src / f"{name}_shipments.csv", skip_blank_lines=False)
    steps.append((f"{name.capitalize()}_Source", df.copy()))
    return df


# --------------------------------------------------------- T: transform
def transform(df: pd.DataFrame, name: str, mode_key: int, steps: list) -> pd.DataFrame:
    """Shape, then text, then type, then keys -- in that order, per source."""
    tag = name.capitalize()

    # --- shape: the road file alone carries junk rows, in TWO named steps.
    # Two different decisions, so two steps: a blank row is dirt in every
    # pipeline that will ever read this file; a TOTAL footer is a grain
    # problem (information to a human, a different grain to a fact table).
    if name == "road":
        # Step 1: drop the blank line -- where trip_id is null/blank.
        raw_ids = df["trip_id"]
        blank = raw_ids.isna() | (raw_ids.astype(str).str.strip() == "")
        df = df[~blank].copy()
        steps.append(("Road_RemovedBlankRows", df.copy()))
        # Step 2: drop the TOTAL footer -- where trip_id == 'TOTAL'.
        # (Verified in the raw CSV: the TOTAL row carries trip_id='TOTAL',
        # weight 83,000 / cost 652,000 on night 1; it is NOT a shipment.)
        total = df["trip_id"].astype(str).str.strip().str.upper() == "TOTAL"
        df = df[~total].copy()
        steps.append(("Road_RemovedTotalRow", df.copy()))

    # --- rename into the seven-name contract, BEFORE any append.
    df = df.rename(columns=RENAME[name])
    steps.append((f"{tag}_RenamedToContract", df.copy()))

    # --- text: Text.Clean equivalent on all text columns, then Proper Case.
    for col in ("OriginRaw", "DestRaw"):
        df[col] = _clean_text(df[col])
    steps.append((f"{tag}_TrimmedTextColumns", df.copy()))
    # StatusRaw: clean, then trim, then Capitalize Each Word -- 17 strings
    # become exactly 3. .str.title() on "IN TRANSIT" gives "In Transit" and
    # on "DELIVERED" gives "Delivered", which is what the star stores.
    df["StatusRaw"] = _clean_text(df["StatusRaw"]).str.title()
    steps.append((f"{tag}_CleanedStatusProperCase", df.copy()))

    if name == "rail":
        # The one place all week a value's meaning is changed on purpose:
        # bare consignment integers (45012) become the star's RL-45012.
        df["ShipmentID"] = "RL-" + df["ShipmentID"].astype(int).astype(str)
        steps.append(("Rail_PrefixedRailIDs", df.copy()))
    else:
        # Uppercase IDs so dedup and joins compare correctly: rd-501 -> RD-501.
        df["ShipmentID"] = _clean_text(df["ShipmentID"]).str.upper()
        steps.append((f"{tag}_UppercasedShipmentIDs", df.copy()))

    # --- type and units: dates with the format STATED, never guessed.
    if name == "marine":
        # dd/MM/yyyy text. format="%d/%m/%Y" is the Using-Locale (en-GB)
        # equivalent: a bare parse would read 01/02/2026 as 2026-01-02
        # (month-first) and spread the 12 rows over eleven months.
        df["ShipDate"] = pd.to_datetime(df["ShipDate"], format="%d/%m/%Y")
    elif name == "road":
        # ISO text dates.
        df["ShipDate"] = pd.to_datetime(df["ShipDate"], format="%Y-%m-%d")
    else:
        # Rail dates are real Excel datetimes, not text: nothing to parse
        # and no format to guess -- just confirm the datetime type.
        assert pd.api.types.is_datetime64_any_dtype(df["ShipDate"])
        df["ShipDate"] = df["ShipDate"].astype("datetime64[ns]")
    df["FreightCostPKR"] = df["FreightCostPKR"].astype("int64")
    steps.append((f"{tag}_TypedShipDatesAndCosts", df.copy()))

    if name == "rail":
        # Tons to kilograms, in a step of its own, BEFORE the append, so the
        # rail subtotal already reads 295,250 kg before anything is combined.
        df["WeightKG"] = (df["WeightTons"] * 1000).astype("int64")
        df = df.drop(columns=["WeightTons"])
        steps.append(("Rail_ConvertedTonsToKG", df.copy()))
    else:
        df["WeightKG"] = df["WeightKG"].astype("int64")

    # ModeKey, which no source carries: assigned from which query the row
    # came through, per source and before the append.
    df["ModeKey"] = mode_key
    steps.append((f"{tag}_AddedModeKey", df.copy()))

    df = df[CONTRACT + ["ModeKey"]]
    steps.append((f"{tag}_ContractReady", df.copy()))
    return df


# ------------------------------------------------------- L: load (combined)
def run_pipeline(src: Path, label: str) -> dict:
    """Run the whole pipeline against one source folder.

    Returns the published FactShipment, the per-source frames, the STEPS
    list, the appended (pre-dedup) row count, and the -1 counts measured
    after each left merge (counted, never asserted away -- see check 6).
    """
    global STEPS, STEPS_BY_NAME
    dims = load_dimensions()
    mode_map = {"Marine": 1, "Rail": 2, "Road": 3}
    steps: list = []

    parts = {}
    for name in ("marine", "rail", "road"):
        raw = extract(src, name, steps)
        parts[name] = transform(raw, name, mode_map[MODE_NAMES[name]], steps)

    # Append, THEN deduplicate -- in that order, on the business key, after
    # the trim and the uppercase (see the dedup-before-trim note below).
    fact = pd.concat(parts.values(), ignore_index=True)
    steps.append(("AppendedFactStaging", fact.copy()))
    appended = len(fact)
    appended_weight = int(fact["WeightKG"].sum())
    appended_cost = int(fact["FreightCostPKR"].sum())

    # NOTE (not a second pipeline, just the contrast the refresh test is
    # about): deduplicating BEFORE the trim would leave the trailing-space
    # second RD-508 alive on night 2 and report 36 rows / 591,550 kg
    # instead of 35 / 584,950. That is why trim-before-dedup is a
    # constraint, not a preference -- and why nothing on night 1's file,
    # where the two RD-505 rows are byte-identical, would ever show it.
    fact = fact.drop_duplicates(subset=["ShipmentID"])
    steps.append(("RemovedDuplicatesOnShipmentID", fact.copy()))

    # Conform the names, then trade them for keys: left outer, twice, and
    # keep every row. An inner join would drop unmatched shipments and move
    # the totals; a left join keeps the row and makes the problem visible.
    alias = build_alias_lookup(dims["DimCity"])
    steps.append(("BuiltAliasLookup", alias.copy()))

    null_counts = {}
    for raw_col, key_col, tag in (("OriginRaw", "OriginCityKey", "Origin"),
                                  ("DestRaw", "DestCityKey", "Dest")):
        fact = fact.merge(alias.rename(columns={"RawSpelling": raw_col,
                                                "CityKey": key_col}),
                          on=raw_col, how="left")
        steps.append((f"Merged{tag}CityKeyLeftOuter", fact.copy()))
        # Count the nulls AFTER this left merge, BEFORE filling: this is
        # the -1 count for this side, measured while it is still visible.
        null_counts[key_col] = int(fact[key_col].isna().sum())
        fact[key_col] = fact[key_col].fillna(-1).astype("int64")
        steps.append((f"Filled{tag}CityKeyUnknown", fact.copy()))

    fact = fact[CONTRACT + ["ModeKey", "OriginCityKey", "DestCityKey"]]
    fact = fact.reset_index(drop=True)
    steps.append(("PublishedFactShipment", fact.copy()))

    # Rows routed to Unknown (-1 on either side). -1 can only have come from
    # the fill above: the alias map contains no -1 key (the Unknown row's
    # blank alias is filtered out when the map is built).
    minus_one = int(((fact["OriginCityKey"] == -1)
                     | (fact["DestCityKey"] == -1)).sum())
    unresolved = sorted(set(fact.loc[fact["OriginCityKey"] == -1, "OriginRaw"])
                        | set(fact.loc[fact["DestCityKey"] == -1, "DestRaw"]))

    STEPS = steps
    STEPS_BY_NAME = dict(steps)
    return {"fact": fact, "parts": parts, "steps": steps, "dims": dims,
            "appended": appended, "appended_weight": appended_weight,
            "appended_cost": appended_cost, "null_counts": null_counts,
            "minus_one": minus_one, "unresolved": unresolved, "label": label}


def _print_checks_header(label: str) -> None:
    print(f"\n===== {label} =====")


def run_night1() -> dict:
    """Night 1: the eight checks, as assertions -- except check 6, which is
    COUNTED and PRINTED, never asserted (see the note under check 6)."""
    r = run_pipeline(SOURCE, "night1")
    fact, parts = r["fact"], r["parts"]
    _print_checks_header("Night 1 -- data/source/ (the eight checks)")

    # Per-source subtotals, before the append (the T4 ladder).
    for name, rows, weight, cost in (("marine", 12, 206_500, 1_618_000),
                                     ("rail", 10, 295_250, 2_585_000),
                                     ("road", 13, 83_000, 652_000)):
        p = parts[name]
        assert len(p) == rows, (name, len(p))
        assert int(p["WeightKG"].sum()) == weight, (name, p["WeightKG"].sum())
        assert int(p["FreightCostPKR"].sum()) == cost, (name, p["FreightCostPKR"].sum())
        print(f"  {name}: {len(p)} rows, {int(p['WeightKG'].sum()):,} kg, "
              f"PKR {int(p['FreightCostPKR'].sum()):,}")
    print(f"  appended: {r['appended']} rows, {r['appended_weight']:,} kg, "
          f"PKR {r['appended_cost']:,}")
    assert (r["appended"], r["appended_weight"], r["appended_cost"]) == \
        (35, 584_750, 4_855_000)

    # Check 1 -- contract columns in each of the three queries: 7.
    for name, p in parts.items():
        assert list(p.columns[:7]) == CONTRACT, (name, list(p.columns))
    print("Check 1 PASS -- contract columns in each query: 7 "
          f"({', '.join(CONTRACT)})")

    # Check 2 -- rail subtotal in kg (tons already converted, pre-append).
    rail_kg = int(fact[fact["ModeKey"] == 2]["WeightKG"].sum())
    assert rail_kg == 295_250, rail_kg
    print(f"Check 2 PASS -- rail subtotal: {rail_kg:,} kg")

    # Check 3 -- distinct StatusRaw values: 3, not 17.
    statuses = sorted(fact["StatusRaw"].unique())
    assert fact["StatusRaw"].nunique() == 3, statuses
    assert statuses == ["Delayed", "Delivered", "In Transit"], statuses
    print(f"Check 3 PASS -- distinct StatusRaw: 3 {statuses}")

    # Check 4 -- latest day in ShipDate: 13 Feb 2026; every date in February
    # (the locale check: a month-first parse would spread these over months).
    assert set(fact["ShipDate"].dt.month) == {2}, set(fact["ShipDate"].dt.month)
    assert int(fact["ShipDate"].dt.day.max()) == 13
    assert fact["ShipDate"].min().date().isoformat() == "2026-02-01"
    assert fact["ShipDate"].max().date().isoformat() == "2026-02-13"
    print("Check 4 PASS -- ShipDate range 2026-02-01 -> 2026-02-13, "
          "latest day = 13 Feb 2026")

    # Check 5 -- rows in FactShipment: 34.
    assert len(fact) == 34, len(fact)
    print(f"Check 5 PASS -- rows in FactShipment: {len(fact)}")

    # Check 6 -- COUNTED and REPORTED, never asserted. The right answer is
    # not a constant: night 1 happens to be 0 only because DimCity's alias
    # map covers all 23 spellings these files contain. An assertion would
    # fail the whole run the first time a new city spelling appears -- and
    # a pipeline that stops for every new spelling is a pipeline nobody
    # runs. Integrity is logging the rows you could not fix.
    print(f"Check 6 (counted, not asserted) -- nulls after each left merge: "
          f"{r['null_counts']}")
    print(f"-1 count night1: {r['minus_one']}")

    # Check 7 -- SUM(WeightKG): 578,650.
    assert int(fact["WeightKG"].sum()) == 578_650, fact["WeightKG"].sum()
    print(f"Check 7 PASS -- SUM(WeightKG): {int(fact['WeightKG'].sum()):,} kg")

    # Check 8 -- SUM(FreightCostPKR): 4,808,000.
    assert int(fact["FreightCostPKR"].sum()) == 4_808_000
    print(f"Check 8 PASS -- SUM(FreightCostPKR): "
          f"PKR {int(fact['FreightCostPKR'].sum()):,}")

    print(f"Published tables: 5 dims imported "
          f"({', '.join(f'{k}={len(v)}' for k, v in r['dims'].items())}) "
          f"+ FactShipment={len(fact)}")
    print(f"STEPS kept: {len(r['steps'])} named intermediate frames "
          f"(inspect via STEPS_BY_NAME, e.g. "
          f"STEPS_BY_NAME['Road_RemovedTotalRow'])")
    print("Night 1: ALL EIGHT CHECKS PASS")
    return r


def run_night2() -> dict:
    """The refresh test: the SAME pipeline functions, pointed at night2.
    Nothing is fixed by hand -- the steps replay on a file never seen."""
    r = run_pipeline(NIGHT2, "night2")
    fact = r["fact"]
    _print_checks_header("Night 2 refresh test -- data/night2/ (same pipeline)")

    assert len(fact) == 35, len(fact)
    print(f"Refresh PASS -- rows in FactShipment: {len(fact)} (was 34)")
    assert int(fact["WeightKG"].sum()) == 584_950, fact["WeightKG"].sum()
    print(f"Refresh PASS -- SUM(WeightKG): {int(fact['WeightKG'].sum()):,} kg")
    assert int(fact["FreightCostPKR"].sum()) == 4_860_000
    print(f"Refresh PASS -- SUM(FreightCostPKR): "
          f"PKR {int(fact['FreightCostPKR'].sum()):,}")
    assert fact["StatusRaw"].nunique() == 3, sorted(fact["StatusRaw"].unique())
    print(f"Refresh PASS -- distinct StatusRaw: 3 "
          f"({sorted(fact['StatusRaw'].unique())}) -- '  DELAYED  ' was "
          f"trimmed and cased by the existing steps, untouched by hand")
    delayed = int((fact["StatusRaw"] == "Delayed").sum())
    assert delayed == 5, delayed
    print(f"Refresh PASS -- rows with status Delayed: {delayed} (was 4)")

    # Check 6, night 2: counted and reported, as on night 1 -- and this time
    # it is not zero. The left outer join kept the row instead of dropping
    # it; it goes to Unknown tonight and is added to DimCity tomorrow,
    # deliberately, with somebody who knows whether Chaman Border is its
    # own city or a border post belonging to Quetta.
    suffix = f" ({', '.join(r['unresolved'])})" if r["unresolved"] else ""
    print(f"-1 count night2: {r['minus_one']}{suffix}")
    assert r["minus_one"] == 1, r["minus_one"]
    assert r["unresolved"] == ["Chaman Border"], r["unresolved"]
    print(f"Refresh PASS -- rows on a -1 city key: {r['minus_one']} "
          f"(unresolved city: {r['unresolved']})")
    print("Note: dedup-before-trim would give 36 rows / 591,550 kg here "
          "(the trailing-space second RD-508 survives) -- this run did NOT "
          "use that order, and no wrong pipeline was run for contrast.")
    print("Night 2 refresh test: ALL ASSERTIONS PASS")
    return r


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PakFreight Week 5 ETL pipeline (night 1 checks / "
                    "night 2 refresh test).")
    parser.add_argument("--night2", action="store_true",
                        help="run only the night-2 refresh test")
    parser.add_argument("--all", action="store_true",
                        help="run night 1, then the night-2 refresh test")
    args = parser.parse_args()
    if args.night2:
        run_night2()
    elif args.all:
        run_night1()
        run_night2()
    else:
        run_night1()


if __name__ == "__main__":
    main()
