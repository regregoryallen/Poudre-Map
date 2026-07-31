"""Side-by-side of the shading variants — for choosing, not for shipping.

The top row is the whole basin, where every variant looks acceptable. The
bottom row is a window over the irrigated plains east of the canyon mouth,
where relief is almost nil. That second row is the actual test: it is where a
mountain-tuned hillshade collapses into grey noise, and it is the reason this
script crops there rather than somewhere prettier.

    python src/terrain_compare.py            # → out/terrain_variants.png
"""

from __future__ import annotations

import sys

import geopandas as gpd
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import rasterio  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402

import sources as S  # noqa: E402
from terrain import TARGET_CRS, TERRAIN_DIR  # noqa: E402

VARIANTS = [
    ("hillshade", "Single sun · 315°/45°"),
    ("hillshade_multi", "Multidirectional · 4 azimuths"),
    ("hypsometric", "Hypsometric tint × relief"),
]

# Plains window, in lon/lat — the flat irrigated country downstream of the
# canyon mouth, roughly Fort Collins to Greeley.
PLAINS_LL = (-105.10, 40.40, -104.62, 40.70)


def read(path, window=None):
    with rasterio.open(path) as ds:
        arr = ds.read(window=window)
        tr = ds.window_transform(window) if window is not None else ds.transform
        bounds = rasterio.windows.bounds(window, ds.transform) if window is not None \
            else ds.bounds
    if arr.shape[0] == 1:
        return arr[0], bounds
    return np.moveaxis(arr, 0, -1), bounds


def main() -> int:
    if not (TERRAIN_DIR / "dem.tif").exists():
        print("data/terrain/dem.tif missing — run src/terrain.py first")
        return 1

    basin = gpd.read_file(S.DERIVED_DIR / "basin.geojson").to_crs(TARGET_CRS)

    # In-basin elevation range, which is narrower than the padded raster's.
    with rasterio.open(TERRAIN_DIR / "dem.tif") as ds:
        from rasterio.mask import mask as rio_mask
        clipped, _ = rio_mask(ds, basin.geometry, crop=True, nodata=np.nan)
        v = clipped[0][np.isfinite(clipped[0])]
        print(f"in-basin elevation: {v.min():.0f}–{v.max():.0f} m "
              f"(relief {v.max() - v.min():.0f} m)")

        plains_win = from_bounds(
            *gpd.GeoSeries.from_xy(
                [PLAINS_LL[0], PLAINS_LL[2]], [PLAINS_LL[1], PLAINS_LL[3]],
                crs=4326).to_crs(TARGET_CRS).total_bounds,
            ds.transform,
        )

    fig, axes = plt.subplots(2, 3, figsize=(18, 12))

    for col, (name, title) in enumerate(VARIANTS):
        path = TERRAIN_DIR / f"{name}.tif"
        if not path.exists():
            continue

        for row, window in enumerate([None, plains_win]):
            ax = axes[row][col]
            arr, bounds = read(path, window)
            extent = (bounds[0], bounds[2], bounds[1], bounds[3])
            kw = {} if arr.ndim == 3 else {"cmap": "gray", "vmin": 0, "vmax": 255}
            ax.imshow(arr, extent=extent, origin="upper", **kw)
            basin.plot(ax=ax, facecolor="none", edgecolor="#d94801",
                       linewidth=1.6 if row == 0 else 2.4)
            ax.set_xlim(extent[0], extent[1])
            ax.set_ylim(extent[2], extent[3])
            ax.set_xticks([]); ax.set_yticks([])
            ax.set_aspect("equal")
            for sp in ax.spines.values():
                sp.set_visible(False)
            if row == 0:
                ax.set_title(title, fontsize=12)

    for row, label in enumerate(["whole basin", "plains detail — the real test"]):
        axes[row][0].text(-0.03, 0.5, label, rotation=90, va="center",
                          ha="right", transform=axes[row][0].transAxes,
                          fontsize=11)

    fig.suptitle(
        "Cache la Poudre — shading variants, 3DEP 1/3 arc-second at 20 m, "
        "EPSG:26913", fontsize=13)
    fig.tight_layout()
    S.ROOT.joinpath("out").mkdir(exist_ok=True)
    out = S.ROOT / "out" / "terrain_variants.png"
    fig.savefig(out, dpi=100, bbox_inches="tight", facecolor="white")
    print(f"wrote {out.relative_to(S.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
