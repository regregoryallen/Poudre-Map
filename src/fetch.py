"""Stage 1 — pull source data into data/cache/.

The only stage that touches the network. Each layer lands in its own
GeoPackage so a single failure doesn't cost you the whole run and one layer
can be refreshed in isolation.

    python src/fetch.py                 # everything missing
    python src/fetch.py --only basin huc12
    python src/fetch.py --refresh       # re-download even if cached
"""

from __future__ import annotations

import argparse
import sys

import geopandas as gpd
import requests

import sources as S


def _bbox_of(gdf: gpd.GeoDataFrame, pad: float = 0.05) -> dict[str, float]:
    minx, miny, maxx, maxy = gdf.total_bounds
    return {
        "xmin": minx - pad,
        "ymin": miny - pad,
        "xmax": maxx + pad,
        "ymax": maxy + pad,
    }


def fetch_basin(cfg: dict) -> gpd.GeoDataFrame:
    wbd = cfg["sources"]["wbd"]
    huc8 = cfg["basin"]["huc8"]
    layer = S.Layer(wbd["base"], wbd["layers"]["huc8"], "basin")
    return S.query(layer, where=f"huc8='{huc8}'")


def fetch_huc(cfg: dict, tier: str) -> gpd.GeoDataFrame:
    wbd = cfg["sources"]["wbd"]
    huc8 = cfg["basin"]["huc8"]
    layer = S.Layer(wbd["base"], wbd["layers"][tier], tier)
    return S.query(layer, where=f"{tier} LIKE '{huc8}%'")


def fetch_nldi_basin(cfg: dict) -> gpd.GeoDataFrame:
    """Hydrologic delineation upstream of the check gage — the cross-check
    against the WBD polygon, not the map's subject."""
    nldi = cfg["sources"]["nldi"]
    gage = nldi["check_gage"]
    url = f"{nldi['base']}/nwissite/{gage}/basin"
    print(f"  nldi_basin: {gage}")
    r = requests.get(url, params={"f": "json"}, timeout=S.TIMEOUT)
    r.raise_for_status()
    gdf = gpd.GeoDataFrame.from_features(r.json()["features"], crs="EPSG:4326")
    gdf["gage"] = gage
    print(f"  nldi_basin: {len(gdf)} features")
    return gdf


def fetch_nhd(cfg: dict, which: str, basin: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    nhd = cfg["sources"]["nhdplus_hr"]
    layer = S.Layer(nhd["base"], nhd["layers"][which], f"nhd_{which}")
    where = "1=1"
    if which == "flowlines":
        where = f"streamorde >= {nhd['min_order']}"
    # Envelope-filter server-side; build.py does the precise clip to the basin.
    # See the out_fields note in sources.yml — `*` here is a performance trap.
    return S.query(
        layer,
        where=where,
        out_fields=nhd["out_fields"][which],
        geometry=_bbox_of(basin),
        page_size=1000,
    )


def _sql_in(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def fetch_gnis(cfg: dict, which: str) -> gpd.GeoDataFrame:
    """Resolve the curated names in config/places.yml to GNIS coordinates.

    Names are matched with county as a disambiguator — there are Lovelands in
    several states, and passes on a county line appear once per county.
    """
    gnis = cfg["sources"]["gnis"]
    places = S.load_config("places.yml")[which]
    layer = S.Layer(gnis["base"], gnis["layers"][which], f"gnis_{which}")

    names = sorted({p["name"] for p in places})
    counties = sorted({p["county"] for p in places})
    where = (f"gaz_name IN ({_sql_in(names)}) "
             f"AND state_alpha='CO' AND county_name IN ({_sql_in(counties)})")

    gdf = S.query(layer, where=where,
                  out_fields="gaz_name,gaz_featureclass,state_alpha,county_name")
    if gdf.empty:
        return gdf

    # A pass on a county line is listed once per county at identical
    # coordinates; keep one.
    gdf = gdf.drop_duplicates(subset=["gaz_name"], keep="first").copy()

    ranks = {p["name"]: p.get("rank", 2) for p in places}
    gdf["rank"] = gdf["gaz_name"].map(ranks).fillna(3).astype(int)
    gdf = gdf.rename(columns={"gaz_name": "name",
                              "gaz_featureclass": "featureclass"})

    missing = sorted(set(names) - set(gdf["name"]))
    if missing:
        print(f"  {which}: NOT FOUND in GNIS — {', '.join(missing)}")
    return gdf


def fetch_highways(cfg: dict, basin: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """One TIGER query per route — they live in different layers."""
    import pandas as pd

    tiger = cfg["sources"]["tiger"]
    routes = S.load_config("places.yml")["highways"]
    bbox = _bbox_of(basin, pad=0.12)

    frames = []
    for r in routes:
        layer = S.Layer(tiger["base"], r["layer"], f"tiger_{r['label']}")
        gdf = S.query(layer, where=f"NAME = '{r['tiger_name']}'",
                      out_fields="NAME,MTFCC", geometry=bbox)
        if gdf.empty:
            print(f"  {r['label']}: no segments returned")
            continue
        gdf["label"] = r["label"]
        gdf["rank"] = r.get("rank", 2)
        frames.append(gdf)

    if not frames:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")


def fetch_states(cfg: dict) -> gpd.GeoDataFrame:
    """State outlines for the print map's locator inset. Only worth having
    because the basin straddles the CO/WY line — the inset is where that
    becomes legible at a glance."""
    base = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
            "/State_County/MapServer")
    layer = S.Layer(base, 0, "states")
    return S.query(layer, where="STUSAB IN ('CO','WY','NE','KS','UT','NM')",
                   out_fields="STUSAB,NAME")


def fetch_csu(cfg: dict, which: str) -> gpd.GeoDataFrame:
    csu = cfg["sources"]["csu"]
    service = csu["layers"][which]
    layer = S.Layer(f"{csu['base']}/{service}/FeatureServer", 0, f"csu_{which}")
    return S.query(layer)


# Order matters: basin is a dependency of the NHD envelope queries.
JOBS: dict[str, object] = {
    "basin": lambda cfg, ctx: fetch_basin(cfg),
    "huc10": lambda cfg, ctx: fetch_huc(cfg, "huc10"),
    "huc12": lambda cfg, ctx: fetch_huc(cfg, "huc12"),
    "nldi_basin": lambda cfg, ctx: fetch_nldi_basin(cfg),
    "nhd_flowlines": lambda cfg, ctx: fetch_nhd(cfg, "flowlines", ctx["basin"]),
    "nhd_waterbodies": lambda cfg, ctx: fetch_nhd(cfg, "waterbodies", ctx["basin"]),
    "nhd_gages": lambda cfg, ctx: fetch_nhd(cfg, "gages", ctx["basin"]),
    "csu_canals": lambda cfg, ctx: fetch_csu(cfg, "canals"),
    "gnis_populated": lambda cfg, ctx: fetch_gnis(cfg, "populated"),
    "gnis_landforms": lambda cfg, ctx: fetch_gnis(cfg, "landforms"),
    "highways": lambda cfg, ctx: fetch_highways(cfg, ctx["basin"]),
    "states": lambda cfg, ctx: fetch_states(cfg),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="+", choices=list(JOBS), metavar="LAYER")
    ap.add_argument("--refresh", action="store_true", help="re-download if cached")
    args = ap.parse_args()

    cfg = S.load_config()
    wanted = args.only or list(JOBS)
    ctx: dict[str, gpd.GeoDataFrame] = {}
    failures: list[str] = []

    # Jobs that need the basin envelope; make sure it's loaded either way.
    needs_basin = [j for j in wanted if j.startswith("nhd_") or j == "highways"]
    if needs_basin and "basin" not in wanted:
        if S.cache_path("basin").exists():
            ctx["basin"] = S.read_cache("basin")
        else:
            wanted = ["basin"] + wanted

    for name in wanted:
        path = S.cache_path(name)
        if path.exists() and not args.refresh:
            print(f"{name}: cached, skipping")
            if name == "basin":
                ctx["basin"] = S.read_cache("basin")
            continue

        print(f"{name}: fetching")
        try:
            gdf = JOBS[name](cfg, ctx)
        except Exception as exc:  # noqa: BLE001 — report and continue
            print(f"{name}: FAILED — {exc}")
            failures.append(name)
            continue

        if gdf.empty:
            print(f"{name}: returned no features — not caching")
            failures.append(name)
            continue

        S.write_cache(gdf, name)
        ctx[name] = gdf
        print(f"{name}: wrote {path.relative_to(S.ROOT)}")

    if failures:
        print(f"\nincomplete: {', '.join(failures)}")
        return 1
    print("\nfetch complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
