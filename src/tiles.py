"""Stage 3a — pack derived GeoJSON into a single PMTiles archive.

One static file, served over plain HTTP range requests. No tile server, no
database. tippecanoe does the per-zoom generalization, which is why build.py
leaves the derived GeoJSON at full detail.

Zoom range is 7–13. Below 7 the basin is a speck; above 13 you are past the
source resolution of the WBD and NHD data and are just serving vertices.

Per-layer *visibility* by zoom is deliberately not baked in here — that belongs
in the MapLibre style, where it stays adjustable without a rebuild. tippecanoe
only decides what geometry exists at each zoom, not what gets drawn.

    python src/tiles.py
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

import sources as S

OUT = S.ROOT / "web" / "poudre.pmtiles"

MIN_ZOOM = 7
MAX_ZOOM = 13

# Order matters only for readability; tippecanoe keeps them as named layers.
LAYERS = [
    "basin",
    "huc10",
    "huc12",
    "nldi_basin",
    "flowlines",
    "waterbodies",
    "canals",
    "gages",
    "highways",
]

# Point labels don't go in the tiles. There are thirteen of them, and MapLibre
# symbol layers need a glyph endpoint — a font-serving dependency that would
# break the "drop three files on a static host" property for the sake of a
# dozen words. HTML markers read from this JSON instead: no glyphs, no build
# step, and the labels are stylable with ordinary CSS.
LABELS_JSON = S.ROOT / "web" / "labels.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true", help="show tippecanoe output")
    args = ap.parse_args()

    if shutil.which("tippecanoe") is None:
        print("tippecanoe not found — apt install tippecanoe")
        return 1

    cmd: list[str] = [
        "tippecanoe",
        "-o", str(OUT),
        "--force",
        "--minimum-zoom", str(MIN_ZOOM),
        "--maximum-zoom", str(MAX_ZOOM),
        # Thin only when a tile actually blows its budget, and thin the densest
        # layer first — which is flowlines, never the boundary polygons. That
        # keeps the basin outline intact at every zoom without having to run
        # separate passes and tile-join them.
        "--drop-densest-as-needed",
        # Small polygons (headwater lakes) should shrink, not get squared up
        # into visible artifacts at low zoom.
        "--no-tiny-polygon-reduction",
        "--attribution", "USGS WBD/NHDPlus HR/NLDI; CSU Geospatial Centroid",
        "--name", "Cache la Poudre watershed",
        "--description", "HUC8 10190007 — basin, subwatersheds, hydrography",
    ]

    present: list[str] = []
    for name in LAYERS:
        path = S.DERIVED_DIR / f"{name}.geojson"
        if not path.exists():
            print(f"note: {name}.geojson missing — skipping")
            continue
        present.append(name)
        cmd += ["-L", f"{name}:{path}"]

    if not present:
        print("no derived GeoJSON found — run build.py first")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    print(f"packing {len(present)} layers: {', '.join(present)}")
    result = subprocess.run(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0 or args.verbose:
        print(result.stdout)
    if result.returncode != 0:
        print(f"tippecanoe failed (exit {result.returncode})")
        return result.returncode

    mb = OUT.stat().st_size / 1024 / 1024
    print(f"wrote {OUT.relative_to(S.ROOT)}  {mb:.1f} MB  z{MIN_ZOOM}-{MAX_ZOOM}")

    write_labels()
    return 0


def write_labels() -> None:
    """Emit web/labels.json — place points plus highway shield anchors."""
    import json

    import geopandas as gpd

    out: dict[str, list] = {"places": [], "shields": []}

    # Reuse the print map's label nudges so the two maps agree about which
    # side a label hangs from. The print offsets are metres; the web only
    # needs the direction, since a pixel gap has to be zoom-independent.
    places_cfg = S.load_config("places.yml")
    hints = {}
    for group in ("populated", "landforms"):
        for p in places_cfg.get(group, []):
            dx, dy = p.get("offset", (900, 700))
            hints[p["name"]] = {
                "anchor": p.get("anchor", "left"),
                "below": dy < 0,
            }

    for name, kind in (("places", "town"), ("landforms", "pass")):
        path = S.DERIVED_DIR / f"{name}.geojson"
        if not path.exists():
            continue
        for _, r in gpd.read_file(path).to_crs(4326).iterrows():
            h = hints.get(r["name"], {"anchor": "left", "below": False})
            out["places"].append({
                "name": r["name"],
                "kind": kind,
                "rank": int(r.get("rank", 2)),
                "in_basin": bool(r.get("in_basin", True)),
                "anchor": h["anchor"],
                "below": h["below"],
                "lon": round(r.geometry.x, 5),
                "lat": round(r.geometry.y, 5),
            })

    hw_path = S.DERIVED_DIR / "highways.geojson"
    if hw_path.exists():
        # Same shield_at fractions the print map uses, so the two agree.
        routes = {r["label"]: r.get("shield_at", 0.5)
                  for r in S.load_config("places.yml").get("highways", [])}
        hw = gpd.read_file(hw_path).to_crs("EPSG:26913")
        for label, grp in hw.groupby("label"):
            merged = grp.geometry.union_all()
            geoms = list(getattr(merged, "geoms", [merged]))
            longest = max(geoms, key=lambda g: g.length)
            at = routes.get(label, 0.5)
            for frac in ([at] if isinstance(at, (int, float)) else at):
                pt = gpd.GeoSeries([longest.interpolate(frac, normalized=True)],
                                   crs="EPSG:26913").to_crs(4326).iloc[0]
                out["shields"].append({"label": label,
                                       "lon": round(pt.x, 5),
                                       "lat": round(pt.y, 5)})

    LABELS_JSON.parent.mkdir(parents=True, exist_ok=True)
    LABELS_JSON.write_text(json.dumps(out, indent=1))
    print(f"wrote {LABELS_JSON.relative_to(S.ROOT)}  "
          f"{len(out['places'])} places, {len(out['shields'])} shields")


if __name__ == "__main__":
    sys.exit(main())
