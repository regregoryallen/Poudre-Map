"""Stage 2b — 3DEP elevation → shaded relief.

Runs once and serves both renderers: a full-resolution GeoTIFF for the print
map, and whatever the web viewer needs downsampled from the same source.

The four 1-degree 3DEP tiles covering this basin total ~1.5 GB, but they are
proper COGs — tiled 512x512 with overviews — so this reads only the windows
that intersect the basin over /vsicurl and never downloads the rest.

Output lands in data/terrain/ (untracked, regenerable):

    dem.tif                 elevation, EPSG:26913, metres
    hillshade.tif           conventional relief, one sun at 315deg/45deg
    hillshade_multi.tif     four-azimuth blend
    hypsometric.tif         RGB elevation tint blended with relief

Why three: the basin spans alpine relief in the Rawah and Never Summer ranges
down to dead-flat irrigated plains east of the canyon mouth. No single sun
angle or z-factor serves both — a setting that reads well in the mountains
turns the plains into noise. These are the three defensible answers; pick by
looking, not by arguing.

    python src/terrain.py                    # 20 m, all variants
    python src/terrain.py --resolution 10
    python src/terrain.py --variants hillshade
"""

from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

# /vsicurl tuning — without these GDAL issues a directory listing per open and
# re-reads blocks it already has.
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("VSI_CACHE", "TRUE")
os.environ.setdefault("GDAL_CACHEMAX", "512")

import rasterio  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402
from rasterio.merge import merge  # noqa: E402
from rasterio.warp import calculate_default_transform, reproject  # noqa: E402

import sources as S  # noqa: E402

TERRAIN_DIR = S.ROOT / "data" / "terrain"

S3 = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF"
      "/current/{tile}/USGS_13_{tile}.tif")

TARGET_CRS = "EPSG:26913"
PAD_DEG = 0.08          # ~9 km of context beyond the basin
DEFAULT_RES = 20.0      # metres; 11x17 at 300 dpi needs ~34 m, so this has room

# Conventional cartographic relief: sun in the upper left. Physically wrong for
# the northern hemisphere, but inverting it makes ridges read as valleys.
AZIMUTH = 315.0
ALTITUDE = 45.0

# Multidirectional weights, after the USGS/Esri approach: a dominant NW sun
# plus three fills that keep slopes facing away from it from going flat.
MULTI = [(225.0, 0.20), (270.0, 0.25), (315.0, 0.35), (360.0, 0.20)]


def tiles_for(bounds: tuple[float, float, float, float]) -> list[str]:
    """3DEP tile names covering a lon/lat box. Tiles are named for their
    NORTH-west corner, so a tile 'n41w106' spans lat 40-41, lon -106..-105."""
    minx, miny, maxx, maxy = bounds
    names = []
    for lat in range(math.floor(miny), math.ceil(maxy)):
        for lon in range(math.floor(minx), math.ceil(maxx)):
            names.append(f"n{lat + 1:02d}w{abs(lon):03d}")
    return names


def basin_bounds() -> tuple[float, float, float, float]:
    import geopandas as gpd

    path = S.DERIVED_DIR / "basin.geojson"
    if not path.exists():
        raise FileNotFoundError("data/derived/basin.geojson missing — "
                                "run src/build.py first")
    minx, miny, maxx, maxy = gpd.read_file(path).total_bounds
    return (minx - PAD_DEG, miny - PAD_DEG, maxx + PAD_DEG, maxy + PAD_DEG)


def build_dem(res: float) -> "rasterio.io.DatasetReader":
    """Window-read the covering tiles, mosaic, and reproject to UTM."""
    bounds = basin_bounds()
    names = tiles_for(bounds)
    print(f"basin + {PAD_DEG}° pad: "
          f"{[round(b, 3) for b in bounds]}")
    print(f"3DEP tiles: {', '.join(names)}")

    srcs = []
    for name in names:
        url = "/vsicurl/" + S3.format(tile=name)
        try:
            srcs.append(rasterio.open(url))
        except rasterio.RasterioIOError as exc:
            print(f"  {name}: unavailable ({exc}) — skipping")
    if not srcs:
        raise RuntimeError("no 3DEP tiles could be opened")

    print(f"mosaicking {len(srcs)} tiles over the basin window…")
    mosaic, transform = merge(srcs, bounds=bounds, nodata=srcs[0].nodata)
    profile = srcs[0].profile
    for s in srcs:
        s.close()

    mosaic = mosaic[0]
    print(f"  source window: {mosaic.shape[1]}x{mosaic.shape[0]} cells "
          f"@ 1/3 arc-second")

    src_crs = profile["crs"]
    nodata = profile.get("nodata", -999999.0)

    dst_transform, width, height = calculate_default_transform(
        src_crs, TARGET_CRS, mosaic.shape[1], mosaic.shape[0],
        *bounds, resolution=res,
    )
    print(f"reprojecting to {TARGET_CRS} at {res:g} m → {width}x{height}")

    dem = np.empty((height, width), dtype="float32")
    reproject(
        source=mosaic, destination=dem,
        src_transform=transform, src_crs=src_crs, src_nodata=nodata,
        dst_transform=dst_transform, dst_crs=TARGET_CRS, dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )

    TERRAIN_DIR.mkdir(parents=True, exist_ok=True)
    out = TERRAIN_DIR / "dem.tif"
    with rasterio.open(
        out, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float32", crs=TARGET_CRS, transform=dst_transform,
        nodata=np.nan, compress="deflate", predictor=3, tiled=True,
    ) as dst:
        dst.write(dem, 1)

    valid = dem[np.isfinite(dem)]
    print(f"wrote {out.relative_to(S.ROOT)}  "
          f"{out.stat().st_size / 1e6:.1f} MB  "
          f"elev {valid.min():.0f}–{valid.max():.0f} m")
    return dem, dst_transform, width, height


def _slope_aspect(dem: np.ndarray, res: float, z_factor: float):
    # np.gradient gives central differences, which is Horn's method to within
    # the edge handling — fine at 10-20 m cells.
    dy, dx = np.gradient(dem, res, res)
    dx *= z_factor
    dy *= z_factor
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(dy, -dx)
    return slope, aspect


def shade(dem: np.ndarray, res: float, azimuth: float, altitude: float,
          z_factor: float) -> np.ndarray:
    slope, aspect = _slope_aspect(dem, res, z_factor)
    zen = math.radians(90.0 - altitude)
    az = math.radians(360.0 - azimuth + 90.0)
    v = (math.cos(zen) * np.cos(slope)
         + math.sin(zen) * np.sin(slope) * np.cos(az - aspect))
    return np.clip(v, 0.0, 1.0)


def multidirectional(dem: np.ndarray, res: float, altitude: float,
                     z_factor: float) -> np.ndarray:
    out = np.zeros_like(dem, dtype="float32")
    for az, w in MULTI:
        out += w * shade(dem, res, az, altitude, z_factor)
    return np.clip(out, 0.0, 1.0)


def hypsometric(dem: np.ndarray, relief: np.ndarray) -> np.ndarray:
    """Elevation tint multiplied by relief.

    This is the variant aimed squarely at the two-terrain problem: the tint
    carries the plains, where there is no relief to shade, and the shading
    carries the mountains, where the tint would otherwise flatten into one
    band of colour.
    """
    lo, hi = np.nanpercentile(dem, [1, 99])
    t = np.clip((dem - lo) / max(hi - lo, 1e-6), 0, 1)

    # Low → dry plains tan; mid → montane green; high → rock grey, then snow.
    stops = np.array([
        [0.00, 0.847, 0.784, 0.667],
        [0.25, 0.725, 0.741, 0.588],
        [0.50, 0.573, 0.647, 0.514],
        [0.72, 0.678, 0.647, 0.588],
        [0.88, 0.792, 0.776, 0.757],
        [1.00, 0.965, 0.965, 0.957],
    ])
    rgb = np.stack([
        np.interp(t, stops[:, 0], stops[:, i + 1]) for i in range(3)
    ], axis=0)

    # Keep some ambient so shadowed faces don't crush to black.
    lit = 0.35 + 0.65 * relief
    return np.clip(rgb * lit, 0, 1)


def write_band(name: str, data: np.ndarray, transform, crs, count: int = 1):
    out = TERRAIN_DIR / f"{name}.tif"
    arr = (np.nan_to_num(data, nan=0.0) * 255).astype("uint8")
    if count == 1:
        arr = arr[np.newaxis, :, :]
    with rasterio.open(
        out, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2],
        count=count, dtype="uint8", crs=crs, transform=transform,
        compress="deflate", predictor=2, tiled=True,
    ) as dst:
        dst.write(arr)
        if count == 3:
            dst.colorinterp = [
                rasterio.enums.ColorInterp.red,
                rasterio.enums.ColorInterp.green,
                rasterio.enums.ColorInterp.blue,
            ]
    print(f"  {name + '.tif':<24} {out.stat().st_size / 1e6:>6.1f} MB")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--resolution", type=float, default=DEFAULT_RES,
                    metavar="M", help=f"output metres/cell (default {DEFAULT_RES:g})")
    ap.add_argument("--z-factor", type=float, default=1.0,
                    help="vertical exaggeration (1.0 = true)")
    ap.add_argument("--variants", nargs="+",
                    choices=["hillshade", "multi", "hypsometric"],
                    default=["hillshade", "multi", "hypsometric"])
    ap.add_argument("--reuse-dem", action="store_true",
                    help="skip the download and reshade the existing dem.tif")
    args = ap.parse_args()

    if args.reuse_dem and (TERRAIN_DIR / "dem.tif").exists():
        print("reusing data/terrain/dem.tif")
        with rasterio.open(TERRAIN_DIR / "dem.tif") as ds:
            dem = ds.read(1)
            transform, res = ds.transform, ds.res[0]
    else:
        dem, transform, _, _ = build_dem(args.resolution)
        res = args.resolution

    print("\nshading:")
    relief = None
    if "hillshade" in args.variants or "hypsometric" in args.variants:
        relief = shade(dem, res, AZIMUTH, ALTITUDE, args.z_factor)
    if "hillshade" in args.variants:
        write_band("hillshade", relief, transform, TARGET_CRS)
    if "multi" in args.variants:
        m = multidirectional(dem, res, ALTITUDE, args.z_factor)
        write_band("hillshade_multi", m, transform, TARGET_CRS)
    if "hypsometric" in args.variants:
        rgb = hypsometric(dem, relief)
        write_band("hypsometric", rgb, transform, TARGET_CRS, count=3)

    print(f"\nterrain written to {TERRAIN_DIR.relative_to(S.ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
