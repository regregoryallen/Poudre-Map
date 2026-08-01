"""Stage 4 — the print map.

Everything here is in EPSG:26913, so the basin has its true shape rather than
Web Mercator's. Extent is the terrain footprint: basin bounds grown by
map_extent.pad_deg from config/sources.yml, the same value terrain.py used, so
the raster and the vectors agree about where the map ends.

Layout is a near-square map with a side column, because the basin is roughly
1.1:1 and a full-bleed 17x11 would either crop it or leave dead margins.

    python src/render.py                     # → out/poudre.png + .pdf
    python src/render.py --base hillshade_multi
    python src/render.py --no-subwatersheds
"""

from __future__ import annotations

import argparse
import sys

import geopandas as gpd
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

import sources as S  # noqa: E402
from terrain import TARGET_CRS, TERRAIN_DIR  # noqa: E402

WY_BORDER_LAT = 41.0

INK = "#1c1917"
WATER = "#1f5f96"
CANAL = "#a8451a"
ACCENT = "#c2410c"
MUTED = "#6b625b"

# Halo so labels survive over both the pale plains and the dark shaded slopes.
def halo(lw=2.6, fg="#ffffff"):
    return [pe.withStroke(linewidth=lw, foreground=fg)]


def place_offsets() -> dict[str, tuple[float, float, str]]:
    """Per-label nudges from config/places.yml, in map-CRS metres."""
    cfg = S.load_config("places.yml")
    out: dict[str, tuple[float, float, str]] = {}
    for group in ("populated", "landforms"):
        for p in cfg.get(group, []):
            dx, dy = p.get("offset", (900, 700))
            out[p["name"]] = (dx, dy, p.get("anchor", "left"))
    return out


def load(name: str) -> gpd.GeoDataFrame | None:
    p = S.DERIVED_DIR / f"{name}.geojson"
    if not p.exists():
        return None
    g = gpd.read_file(p)
    return g.to_crs(TARGET_CRS) if not g.empty else None


def scale_bar(ax, x, y, length_km=20, height=1400):
    """Two-tone bar. Map units are metres, so this is exact."""
    n = 2
    seg = length_km * 1000 / n
    for i in range(n):
        ax.add_patch(Rectangle(
            (x + i * seg, y), seg, height,
            facecolor=INK if i % 2 == 0 else "white",
            edgecolor=INK, linewidth=0.8, zorder=30))
    for i, lab in enumerate(["0", f"{length_km // 2}", f"{length_km} km"]):
        ax.text(x + i * seg, y - height * 1.5, lab, ha="center", va="top",
                fontsize=7.5, color=INK, zorder=30, path_effects=halo(2))


def north_arrow(ax, x, y, size=3200):
    ax.annotate("", xy=(x, y + size), xytext=(x, y),
                arrowprops=dict(arrowstyle="-|>", color=INK, linewidth=1.4),
                zorder=30)
    ax.text(x, y + size * 1.12, "N", ha="center", va="bottom", fontsize=9,
            color=INK, weight="bold", zorder=30, path_effects=halo(2))


def draw_map(ax, cfg, args, layers, extent):
    basin = layers["basin"]

    # --- terrain base -----------------------------------------------------
    base_path = TERRAIN_DIR / f"{args.base}.tif"
    if base_path.exists():
        with rasterio.open(base_path) as ds:
            arr = ds.read()
            b = ds.bounds
        img = np.moveaxis(arr, 0, -1) if arr.shape[0] == 3 else arr[0]
        kw = {} if img.ndim == 3 else {"cmap": "gray", "vmin": 0, "vmax": 255}
        ax.imshow(img, extent=(b.left, b.right, b.bottom, b.top),
                  origin="upper", zorder=0, **kw)
    else:
        print(f"note: {base_path.name} missing — run src/terrain.py")

    # --- everything outside the basin knocked back ------------------------
    # A mask rather than a crop: the neighbouring terrain is context worth
    # keeping, it just should not compete with the subject.
    outside = gpd.GeoDataFrame(
        geometry=[extent.geometry.iloc[0].difference(basin.geometry.union_all())],
        crs=basin.crs)
    outside.plot(ax=ax, facecolor="#ffffff", alpha=0.42, edgecolor="none",
                 zorder=1)

    # --- subwatersheds ----------------------------------------------------
    if not args.no_subwatersheds and layers.get("huc12") is not None:
        layers["huc12"].plot(ax=ax, facecolor="none", edgecolor=INK,
                             linewidth=0.35, alpha=0.30, zorder=2)

    # --- hydrography ------------------------------------------------------
    if layers.get("waterbodies") is not None:
        layers["waterbodies"].plot(ax=ax, facecolor=WATER, edgecolor="none",
                                   alpha=0.85, zorder=3)
    fl = layers.get("flowlines")
    if fl is not None:
        nat = fl[fl["natural"]] if "natural" in fl.columns else fl
        for order, lw in ((2, 0.18), (3, 0.32), (4, 0.55), (5, 0.85),
                          (6, 1.25), (7, 1.9)):
            sub = nat[nat["streamorde"] == order]
            if not sub.empty:
                sub.plot(ax=ax, color=WATER, linewidth=lw, zorder=4)
        top = nat[nat["streamorde"] >= 8]
        if not top.empty:
            top.plot(ax=ax, color=WATER, linewidth=2.4, zorder=4)

    if layers.get("canals") is not None:
        layers["canals"].plot(ax=ax, color=CANAL, linewidth=0.65, alpha=0.9,
                              zorder=5)

    # --- highways ---------------------------------------------------------
    # Cased line: dark casing under a light fill, so the route stays legible
    # over both the pale plains and the dark shaded slopes in the canyon.
    hw = layers.get("highways")
    if hw is not None:
        hw.plot(ax=ax, color="#44403c", linewidth=2.8, alpha=0.9, zorder=6)
        hw.plot(ax=ax, color="#f5f5f4", linewidth=1.3, alpha=1.0, zorder=7)

    # --- basin outline and the state line ---------------------------------
    basin.plot(ax=ax, facecolor="none", edgecolor=INK, linewidth=2.2, zorder=8)

    border = gpd.GeoDataFrame(
        geometry=gpd.GeoSeries.from_wkt(
            [f"LINESTRING(-106.6 {WY_BORDER_LAT}, -104.2 {WY_BORDER_LAT})"]),
        crs=4326).to_crs(TARGET_CRS)
    border = gpd.clip(border, extent)
    border.plot(ax=ax, color=ACCENT, linewidth=1.1, linestyle=(0, (5, 3)),
                zorder=9)

    x0, y0, x1, y1 = extent.total_bounds
    by = border.geometry.iloc[0].coords[0][1]
    ax.text(x1 - (x1 - x0) * 0.012, by + 900, "W Y O M I N G", ha="right",
            va="bottom", fontsize=8.5, color=ACCENT, weight="bold",
            zorder=9, path_effects=halo(2.4))
    ax.text(x1 - (x1 - x0) * 0.012, by - 900, "C O L O R A D O", ha="right",
            va="top", fontsize=8.5, color=ACCENT, weight="bold",
            zorder=9, path_effects=halo(2.4))

    # --- highway shields --------------------------------------------------
    if hw is not None:
        for label, grp in hw.groupby("label"):
            merged = grp.geometry.union_all()
            geoms = list(getattr(merged, "geoms", [merged]))
            longest = max(geoms, key=lambda g: g.length)
            pt = longest.interpolate(0.5, normalized=True)
            ax.text(pt.x, pt.y, label, fontsize=8, weight="bold",
                    color="#27272a", ha="center", va="center", zorder=20,
                    bbox=dict(boxstyle="round,pad=0.28", facecolor="#fafaf9",
                              edgecolor="#3f3f46", linewidth=0.9))

    # --- places -----------------------------------------------------------
    # Label placement is config-driven rather than automatic: matplotlib has no
    # collision avoidance, and for a fixed set of a dozen labels an explicit
    # offset in config/places.yml beats a solver you have to argue with.
    sizes = {1: 9.0, 2: 7.6, 3: 6.6}
    msizes = {1: 34, 2: 20, 3: 12}
    offsets = place_offsets()

    places = layers.get("places")
    if places is not None:
        for _, r in places.iterrows():
            rank = int(r["rank"])
            inb = bool(r.get("in_basin", True))
            dx, dy, anchor = offsets.get(r["name"], (900, 700, "left"))
            ax.scatter([r.geometry.x], [r.geometry.y], s=msizes[rank],
                       facecolor="#ffffff" if not inb else INK,
                       edgecolor=INK, linewidth=0.9, zorder=21)
            ax.text(r.geometry.x + dx, r.geometry.y + dy, r["name"],
                    fontsize=sizes[rank], color=INK if inb else MUTED,
                    style="normal" if inb else "italic",
                    weight="bold" if rank == 1 else "normal",
                    ha=anchor, va="bottom" if dy >= 0 else "top",
                    zorder=22, path_effects=halo())

    lf = layers.get("landforms")
    if lf is not None:
        for _, r in lf.iterrows():
            dx, dy, anchor = offsets.get(r["name"], (1000, 0, "left"))
            ax.scatter([r.geometry.x], [r.geometry.y], s=42, marker="^",
                       facecolor="#ffffff", edgecolor=INK, linewidth=1.0,
                       zorder=21)
            ax.text(r.geometry.x + dx, r.geometry.y + dy, r["name"],
                    fontsize=7.8, color=MUTED, style="italic", ha=anchor,
                    va="bottom" if dy >= 0 else "top", zorder=22,
                    path_effects=halo())

    # --- furniture --------------------------------------------------------
    span = x1 - x0
    scale_bar(ax, x0 + span * 0.04, y0 + (y1 - y0) * 0.045)
    north_arrow(ax, x1 - span * 0.055, y0 + (y1 - y0) * 0.05)

    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_edgecolor(INK); sp.set_linewidth(0.8)


def draw_inset(ax, layers):
    """CO/WY locator. Earns its space because the basin straddles the line —
    that fact is hard to read at map scale and obvious at state scale.

    Everything here is forced into one CRS. The main map works in UTM 13N and
    the layers arrive that way, so the inset has to convert deliberately or it
    silently plots degrees against metres and draws nothing you can see.
    """
    states = layers.get("states")
    basin = layers.get("basin")
    if states is None or basin is None:
        ax.axis("off")
        return

    ins_crs = "EPSG:5070"          # Albers equal-area, sane shapes at state scale
    states = states.to_crs(ins_crs)
    basin = basin.to_crs(ins_crs)
    focus = states[states["STUSAB"].isin(["CO", "WY"])]

    states.plot(ax=ax, facecolor="#eeeae4", edgecolor="#c9c2b9", linewidth=0.5)
    focus.plot(ax=ax, facecolor="#ffffff", edgecolor=MUTED, linewidth=0.9)
    basin.plot(ax=ax, facecolor=ACCENT, edgecolor=ACCENT, linewidth=0.8,
               alpha=0.9)

    b = focus.total_bounds
    padx = (b[2] - b[0]) * 0.05
    pady = (b[3] - b[1]) * 0.05
    ax.set_xlim(b[0] - padx, b[2] + padx)
    ax.set_ylim(b[1] - pady, b[3] + pady)
    ax.set_aspect("equal")

    for code in ("WY", "CO"):
        g = focus[focus.STUSAB == code]
        if g.empty:
            continue
        c = g.geometry.union_all().representative_point()
        ax.text(c.x, c.y, code, fontsize=7, color=MUTED, ha="center",
                va="center", weight="bold")

    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_edgecolor("#c9c2b9"); sp.set_linewidth(0.7)


def draw_column(fig, gs, layers, args):
    ax = fig.add_subplot(gs[0, 1])
    ax.axis("off")

    ax.text(0, 1.0, "Cache la Poudre", fontsize=25, weight="light",
            color=INK, va="top", transform=ax.transAxes)
    ax.text(0, 0.955, "River Watershed", fontsize=25, weight="light",
            color=INK, va="top", transform=ax.transAxes)
    ax.text(0, 0.912, "USGS HUC8 10190007  ·  northern Colorado and Wyoming",
            fontsize=8.4, color=MUTED, va="top", transform=ax.transAxes)
    ax.plot([0, 1], [0.898, 0.898], color="#d6cfc6", linewidth=0.8,
            transform=ax.transAxes, clip_on=False)

    body = (
        "The Poudre rises on the Continental Divide in Rocky Mountain National\n"
        "Park and falls 2,743 m to meet the South Platte at Greeley. Below the\n"
        "canyon mouth at Laporte it becomes one of the most heavily engineered\n"
        "rivers in the West: the orange network is diversion, not drainage."
    )
    ax.text(0, 0.868, body, fontsize=8.3, color=INK, va="top", linespacing=1.65,
            transform=ax.transAxes)

    rows = [
        ("Area", "4,892.7 km²  (1,889 mi²)"),
        ("Colorado", "4,581.8 km²  ·  93.6%"),
        ("Wyoming", "311.0 km²  ·  6.4%"),
        ("Elevation", "1,389 – 4,132 m"),
        ("Relief", "2,743 m"),
        ("Subwatersheds", "53 HUC12  ·  10 HUC10"),
    ]
    y = 0.735
    for k, v in rows:
        ax.text(0, y, k, fontsize=8, color=MUTED, va="top",
                transform=ax.transAxes)
        ax.text(1, y, v, fontsize=8, color=INK, va="top", ha="right",
                transform=ax.transAxes)
        y -= 0.030

    # --- legend -----------------------------------------------------------
    y -= 0.022
    ax.text(0, y, "LEGEND", fontsize=7.6, color=MUTED, va="top",
            weight="bold", transform=ax.transAxes)
    y -= 0.030

    # (kind, style-kwargs, label). Lines get a short rule, points a marker.
    entries = [
        ("line", dict(color=INK, lw=2.0), "Watershed boundary"),
        ("line", dict(color=ACCENT, lw=1.1, ls=(0, (4, 2))),
         "41°N — Colorado / Wyoming"),
        ("line", dict(color=INK, lw=0.5, alpha=0.45), "HUC12 subwatershed"),
        ("line", dict(color=WATER, lw=1.8), "Stream, by Strahler order"),
        ("line", dict(color=CANAL, lw=1.1), "Canal or ditch"),
        ("cased", dict(color="#44403c", lw=2.8), "Highway"),
        ("point", dict(marker="o", mfc=INK, mec=INK, ms=5), "Town, in basin"),
        ("point", dict(marker="o", mfc="#ffffff", mec=INK, ms=5),
         "Town, outside basin"),
        ("point", dict(marker="^", mfc="#ffffff", mec=INK, ms=6), "Pass"),
    ]
    for kind, style, label in entries:
        yy = y - 0.007
        if kind == "line":
            ax.add_line(Line2D([0.005, 0.075], [yy, yy],
                               transform=ax.transAxes, clip_on=False, **style))
        elif kind == "cased":
            # Mirror the two-pass draw used on the map itself.
            ax.add_line(Line2D([0.005, 0.075], [yy, yy],
                               transform=ax.transAxes, clip_on=False, **style))
            ax.add_line(Line2D([0.005, 0.075], [yy, yy], color="#f5f5f4",
                               lw=1.3, transform=ax.transAxes, clip_on=False))
        else:
            ax.add_line(Line2D([0.040], [yy], transform=ax.transAxes,
                               clip_on=False, linestyle="none", **style))
        ax.text(0.10, y, label, fontsize=7.6, color=INK, va="top",
                transform=ax.transAxes)
        y -= 0.0265

    # --- hypsometric ramp -------------------------------------------------
    if args.base == "hypsometric":
        y -= 0.012
        ax.text(0, y, "ELEVATION", fontsize=7.6, color=MUTED, va="top",
                weight="bold", transform=ax.transAxes)
        y -= 0.028
        from terrain import hypsometric
        # hypsometric() returns channels-first; imshow wants channels-last.
        ramp = np.moveaxis(
            hypsometric(np.linspace(1389, 4132, 256)[np.newaxis, :],
                        np.ones((1, 256))), 0, -1)
        ax.imshow(ramp, extent=(0, 1, y - 0.020, y), aspect="auto",
                  transform=ax.transAxes, clip_on=False, zorder=5)
        ax.text(0, y - 0.026, "1,389 m", fontsize=7, color=MUTED, va="top",
                transform=ax.transAxes)
        ax.text(1, y - 0.026, "4,132 m", fontsize=7, color=MUTED, va="top",
                ha="right", transform=ax.transAxes)
        y -= 0.052

    note = (
        "Horsetooth Reservoir and Carter Lake lie inside the basin but import\n"
        "water from the Colorado River basin via the Colorado-Big Thompson\n"
        "Project: the watershed is not a closed hydrologic system.\n\n"
        "Boundary and hydrography: USGS Watershed Boundary Dataset and\n"
        "NHDPlus High Resolution. Relief: USGS 3DEP 1/3 arc-second. Canals:\n"
        "CSU Geospatial Centroid. Places: USGS GNIS. Roads: Census TIGER/Line.\n"
        "NAD83 / UTM zone 13N."
    )
    ax.text(0, 0.043, note, fontsize=6.4, color=MUTED, va="bottom",
            linespacing=1.6, transform=ax.transAxes)
    return ax


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="hypsometric",
                    choices=["hypsometric", "hillshade", "hillshade_multi",
                             "none"])
    ap.add_argument("--no-subwatersheds", action="store_true")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--formats", nargs="+", default=["png", "pdf"])
    args = ap.parse_args()

    cfg = S.load_config()
    names = ["basin", "huc10", "huc12", "flowlines", "waterbodies", "canals",
             "places", "landforms", "highways", "states", "gages"]
    layers = {n: load(n) for n in names}
    if layers["basin"] is None:
        print("run src/build.py first")
        return 1

    from shapely.geometry import box as shp_box
    pad = cfg["map_extent"]["pad_deg"]
    b = layers["basin"].to_crs(4326).total_bounds
    extent = gpd.GeoDataFrame(
        geometry=[shp_box(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)],
        crs=4326).to_crs(TARGET_CRS)

    fig = plt.figure(figsize=(17, 11))
    fig.patch.set_facecolor("#ffffff")
    gs = fig.add_gridspec(1, 2, width_ratios=[1.72, 1], left=0.022,
                          right=0.978, top=0.968, bottom=0.032, wspace=0.045)

    ax = fig.add_subplot(gs[0, 0])
    draw_map(ax, cfg, args, layers, extent)
    draw_column(fig, gs, layers, args)

    # Locator sits in the map's northwest corner — empty terrain there, and
    # the side column is already full. Positioned off the map axes so it
    # follows the layout rather than a hardcoded figure coordinate.
    mpos = ax.get_position()
    iw = mpos.width * 0.19
    ih = iw * (fig.get_figwidth() / fig.get_figheight()) * 0.62
    inset = fig.add_axes([mpos.x0 + mpos.width * 0.018,
                          mpos.y1 - ih - mpos.height * 0.022, iw, ih])
    inset.set_facecolor("#ffffff")
    inset.patch.set_alpha(0.88)
    draw_inset(inset, layers)

    S.ROOT.joinpath("out").mkdir(exist_ok=True)
    for fmt in args.formats:
        out = S.ROOT / "out" / f"poudre.{fmt}"
        fig.savefig(out, dpi=args.dpi, facecolor="white",
                    bbox_inches="tight", pad_inches=0.22)
        print(f"wrote {out.relative_to(S.ROOT)}  "
              f"{out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
