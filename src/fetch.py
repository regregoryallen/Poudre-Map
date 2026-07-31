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

    # The NHD jobs need the basin envelope; make sure it's loaded either way.
    if any(j.startswith("nhd_") for j in wanted) and "basin" not in wanted:
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
