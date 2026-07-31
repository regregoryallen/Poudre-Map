"""Stage 2 — reconcile cached sources into data/derived/.

Deterministic and offline. Reprojects, clips to the basin, validates against
the expectations in config/sources.yml, and writes EPSG:4326 GeoJSON that both
renderers consume.

The validation is not ceremony. The Poudre basin crosses into Wyoming, and
every plausible failure here — a Colorado-only source, a bad clip, a bbox
typo — expresses itself as a basin that still looks entirely reasonable while
being wrong. So the build asserts the Wyoming portion survives and refuses to
write output if it doesn't.

    python src/build.py
    python src/build.py --simplify 0.0002
"""

from __future__ import annotations

import argparse
import sys

import geopandas as gpd
from shapely.geometry import box

import sources as S

# Colorado's northern border is the 41st parallel, so the basin's CO/WY split
# is exactly a cut at y=41.0 — no state-boundary layer needed.
WY_BORDER_LAT = 41.0

# Areas are computed in NAD83 / UTM 13N, not in degrees.
AREA_CRS = "EPSG:26913"


class ValidationError(Exception):
    pass


def area_sqkm(gdf: gpd.GeoDataFrame) -> float:
    return float(gdf.to_crs(AREA_CRS).area.sum() / 1e6)


def split_at_border(gdf: gpd.GeoDataFrame) -> tuple[float, float]:
    """Return (colorado_sqkm, wyoming_sqkm) for a polygon layer."""
    minx, miny, maxx, maxy = gdf.total_bounds
    pad = 1.0
    south = box(minx - pad, miny - pad, maxx + pad, WY_BORDER_LAT)
    north = box(minx - pad, WY_BORDER_LAT, maxx + pad, maxy + pad)
    co = gpd.clip(gdf, gpd.GeoDataFrame(geometry=[south], crs=gdf.crs))
    wy = gpd.clip(gdf, gpd.GeoDataFrame(geometry=[north], crs=gdf.crs))
    return (area_sqkm(co) if not co.empty else 0.0,
            area_sqkm(wy) if not wy.empty else 0.0)


def validate(cfg: dict, layers: dict[str, gpd.GeoDataFrame]) -> list[str]:
    """Check the reconciled data against config expectations.

    Returns a list of failures; empty means good.
    """
    exp = cfg["expectations"]
    problems: list[str] = []

    basin = layers["basin"]
    huc12 = layers["huc12"]

    # 1. The basin's northern edge must reach well past the state line.
    north_edge = float(basin.total_bounds[3])
    if north_edge < exp["basin_north_edge_min"]:
        problems.append(
            f"basin north edge {north_edge:.4f}°N < expected "
            f"{exp['basin_north_edge_min']}°N — Wyoming portion is missing"
        )

    # 2. Subwatershed count.
    if len(huc12) != exp["huc12_count"]:
        problems.append(
            f"huc12 count {len(huc12)} != expected {exp['huc12_count']}"
        )

    # 3. The specific HUC12s that carry the Wyoming signal.
    touching = huc12[huc12["states"].str.contains("WY", na=False)]
    if len(touching) != exp["huc12_touching_wy"]:
        problems.append(
            f"huc12s touching WY: {len(touching)} != expected "
            f"{exp['huc12_touching_wy']}"
        )
    for code in exp["huc12_entirely_wy"]:
        row = huc12[huc12["huc12"] == code]
        if row.empty:
            problems.append(f"huc12 {code} (entirely WY) is absent")
        elif row.iloc[0]["states"] != "WY":
            problems.append(
                f"huc12 {code} states={row.iloc[0]['states']!r}, expected 'WY'"
            )

    # 4. The Wyoming land area itself — the invariant the rest of these checks
    #    are really proxies for.
    _, wy = split_at_border(basin)
    if wy < exp["wyoming_sqkm_min"]:
        problems.append(
            f"Wyoming portion {wy:.1f} km² < expected "
            f"{exp['wyoming_sqkm_min']} km² — basin has been clipped"
        )

    # 5. Any layer clipped to the basin must carry features north of 41°N,
    #    or it's a Colorado-only source masquerading as basin-wide.
    for name in ("flowlines", "huc12"):
        gdf = layers.get(name)
        if gdf is None or gdf.empty:
            continue
        if float(gdf.total_bounds[3]) < WY_BORDER_LAT:
            problems.append(
                f"{name}: no features north of {WY_BORDER_LAT}°N — "
                "source is Colorado-only"
            )

    return problems


def build(cfg: dict, simplify: float | None) -> int:
    S.DERIVED_DIR.mkdir(parents=True, exist_ok=True)

    # --- load what the cache has -------------------------------------------
    required = ["basin", "huc10", "huc12"]
    optional = {
        "flowlines": "nhd_flowlines",
        "waterbodies": "nhd_waterbodies",
        "gages": "nhd_gages",
        "canals": "csu_canals",
        "nldi_basin": "nldi_basin",
    }

    layers: dict[str, gpd.GeoDataFrame] = {}
    for name in required:
        layers[name] = S.read_cache(name)
    for out_name, cache_name in optional.items():
        if S.cache_path(cache_name).exists():
            layers[out_name] = S.read_cache(cache_name)
        else:
            print(f"note: {cache_name} not cached — skipping {out_name}")

    basin = layers["basin"]

    # --- clip linework and points to the basin proper ----------------------
    # fetch.py filtered by envelope; this is the precise cut.
    for name in ("flowlines", "waterbodies", "canals", "gages"):
        if name not in layers:
            continue
        before = len(layers[name])
        layers[name] = gpd.clip(layers[name], basin)
        print(f"clip {name}: {before} → {len(layers[name])}")

    # --- validate before writing anything ----------------------------------
    problems = validate(cfg, layers)
    if problems:
        print("\nVALIDATION FAILED")
        for p in problems:
            print(f"  ✗ {p}")
        print("\nNo output written.")
        return 1
    print("\nvalidation passed")

    # --- report ------------------------------------------------------------
    co, wy = split_at_border(basin)
    total = co + wy
    print("\n--- basin ---")
    print(f"  WBD area        {area_sqkm(basin):>9.1f} km²  "
          f"(config says {cfg['basin']['area_sqkm']})")
    print(f"  Colorado        {co:>9.1f} km²  ({co / total * 100:.1f}%)")
    print(f"  Wyoming         {wy:>9.1f} km²  ({wy / total * 100:.1f}%)")
    print(f"  bounds          {basin.total_bounds.round(4).tolist()}")

    if "nldi_basin" in layers:
        nldi = layers["nldi_basin"]
        inter = gpd.overlay(
            basin[["geometry"]], nldi[["geometry"]], how="intersection"
        )
        print("\n--- WBD vs NLDI (gage-derived) ---")
        print(f"  NLDI area       {area_sqkm(nldi):>9.1f} km²")
        print(f"  overlap         {area_sqkm(inter):>9.1f} km²  "
              f"({area_sqkm(inter) / area_sqkm(basin) * 100:.1f}% of WBD)")

    # --- write -------------------------------------------------------------
    print("\n--- derived ---")
    for name, gdf in layers.items():
        out = gdf
        if simplify and out.geom_type.isin(
            ["Polygon", "MultiPolygon", "LineString", "MultiLineString"]
        ).all():
            out = out.copy()
            out["geometry"] = out.geometry.simplify(simplify, preserve_topology=True)
        path = S.DERIVED_DIR / f"{name}.geojson"
        out.to_file(path, driver="GeoJSON")
        kb = path.stat().st_size / 1024
        print(f"  {name:<14} {len(out):>6} features  {kb:>9.1f} KB")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--simplify",
        type=float,
        default=None,
        metavar="TOL",
        help="simplify tolerance in degrees; leave off for the web path, "
             "where tippecanoe generalizes per zoom and does it better",
    )
    args = ap.parse_args()
    return build(S.load_config(), args.simplify)


if __name__ == "__main__":
    sys.exit(main())
