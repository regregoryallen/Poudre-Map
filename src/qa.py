"""Diagnostic plot of the derived data — deliberately unstyled.

This is not render.py. There is no cartography here on purpose: the job is to
show whether the geometry is right, so anything that would flatter it is left
out. Draws in EPSG:26913 so shapes are true, and marks the 41°N state line
because that is the edge most likely to be silently wrong.

    python src/qa.py            # → out/qa.png
"""

from __future__ import annotations

import sys

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import sources as S  # noqa: E402

PLOT_CRS = "EPSG:26913"
WY_BORDER_LAT = 41.0


def load(name: str) -> gpd.GeoDataFrame | None:
    path = S.DERIVED_DIR / f"{name}.geojson"
    if not path.exists():
        print(f"note: {name}.geojson missing")
        return None
    return gpd.read_file(path).to_crs(PLOT_CRS)


def border_line(ref: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The 41st parallel across the basin's width, in the plot CRS."""
    from shapely.geometry import LineString

    minx, _, maxx, _ = ref.to_crs(4326).total_bounds
    line = LineString([(minx - 0.2, WY_BORDER_LAT), (maxx + 0.2, WY_BORDER_LAT)])
    return gpd.GeoDataFrame(geometry=[line], crs=4326).to_crs(PLOT_CRS)


def main() -> int:
    basin = load("basin")
    if basin is None:
        print("run build.py first")
        return 1

    huc12 = load("huc12")
    flowlines = load("flowlines")
    waterbodies = load("waterbodies")
    canals = load("canals")
    gages = load("gages")
    nldi = load("nldi_basin")

    fig, axes = plt.subplots(1, 2, figsize=(19, 11))

    # --- left: structure -------------------------------------------------
    ax = axes[0]
    if huc12 is not None:
        huc12.plot(ax=ax, facecolor="none", edgecolor="#999", linewidth=0.6)
        wy = huc12[huc12["states"].str.contains("WY", na=False)]
        wy.plot(ax=ax, facecolor="#d94801", edgecolor="#d94801", alpha=0.22,
                linewidth=0.8)
    basin.plot(ax=ax, facecolor="none", edgecolor="black", linewidth=2.0)
    border_line(basin).plot(ax=ax, color="#d94801", linewidth=1.4, linestyle="--")
    ax.set_title(
        "HUC12 subwatersheds — WY-designated shaded\n"
        "dashed line = 41°N (CO/WY border)", fontsize=11
    )

    # --- right: hydrography ----------------------------------------------
    ax = axes[1]
    if flowlines is not None:
        # Width by Strahler order so the trunk is legible at this scale.
        for order, width in ((2, 0.15), (3, 0.3), (4, 0.55), (5, 0.9), (6, 1.4)):
            sub = flowlines[flowlines["streamorde"] == order]
            if not sub.empty:
                sub.plot(ax=ax, color="#2b6cb0", linewidth=width)
        top = flowlines[flowlines["streamorde"] >= 7]
        if not top.empty:
            top.plot(ax=ax, color="#2b6cb0", linewidth=2.0)
    if waterbodies is not None:
        waterbodies.plot(ax=ax, facecolor="#2b6cb0", edgecolor="none", alpha=0.7)
    if canals is not None:
        canals.plot(ax=ax, color="#c05621", linewidth=0.6)
    if gages is not None and not gages.empty:
        gages.plot(ax=ax, color="black", markersize=22, marker="^", zorder=5)
    basin.plot(ax=ax, facecolor="none", edgecolor="black", linewidth=2.0)
    if nldi is not None:
        nldi.plot(ax=ax, facecolor="none", edgecolor="#d94801", linewidth=1.2,
                  linestyle=":")
    border_line(basin).plot(ax=ax, color="#d94801", linewidth=1.0, linestyle="--")
    ax.set_title(
        "flowlines by Strahler order (blue) · canals (orange) · gages (▲)\n"
        "dotted outline = NLDI gage-derived basin", fontsize=11
    )

    for ax in axes:
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

    fig.suptitle(
        "Cache la Poudre — HUC8 10190007 — QA, unstyled, EPSG:26913",
        fontsize=13,
    )
    fig.tight_layout()

    S.ROOT.joinpath("out").mkdir(exist_ok=True)
    out = S.ROOT / "out" / "qa.png"
    fig.savefig(out, dpi=110, bbox_inches="tight", facecolor="white")
    print(f"wrote {out.relative_to(S.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
