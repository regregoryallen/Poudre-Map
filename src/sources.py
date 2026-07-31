"""Config loading and a paginated ArcGIS REST client.

Shared by fetch.py and build.py. Nothing here writes to the cache; this module
only knows how to read config and pull features off a REST endpoint.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import geopandas as gpd
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
CACHE_DIR = ROOT / "data" / "cache"
DERIVED_DIR = ROOT / "data" / "derived"

# NHDPlus HR in particular is slow enough that the default requests timeout is
# useless. These are deliberately generous; a slow response beats a retry storm.
TIMEOUT = 300
RETRIES = 4
BACKOFF = 5


def load_config(name: str = "sources.yml") -> dict[str, Any]:
    with (CONFIG_DIR / name).open() as fh:
        return yaml.safe_load(fh)


@dataclass(frozen=True)
class Layer:
    """One queryable ArcGIS REST layer."""

    base: str
    index: int
    name: str

    @property
    def url(self) -> str:
        return f"{self.base}/{self.index}/query"

    def describe(self) -> dict[str, Any]:
        r = requests.get(
            f"{self.base}/{self.index}", params={"f": "json"}, timeout=TIMEOUT
        )
        r.raise_for_status()
        return r.json()


def _get(url: str, params: dict[str, Any]) -> requests.Response:
    """GET with retry. ArcGIS endpoints fail transiently under load often
    enough that a bare request will lose you a whole fetch run."""
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
            if attempt < RETRIES - 1:
                wait = BACKOFF * (2**attempt)
                print(f"    retry {attempt + 1}/{RETRIES - 1} in {wait}s ({exc})")
                time.sleep(wait)
    raise RuntimeError(f"failed after {RETRIES} attempts: {url}") from last


def _pages(
    layer: Layer,
    where: str,
    out_fields: str,
    geometry: dict[str, Any] | None,
    page_size: int,
    out_sr: int,
) -> Iterator[dict[str, Any]]:
    """Yield GeoJSON FeatureCollections, following ArcGIS pagination.

    ArcGIS signals more-data two different ways depending on version and host,
    so we watch both `exceededTransferLimit` and a full page.
    """
    offset = 0
    while True:
        params: dict[str, Any] = {
            "where": where,
            "outFields": out_fields,
            "outSR": out_sr,
            "returnGeometry": "true",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "f": "geojson",
        }
        if geometry is not None:
            params.update(
                {
                    "geometry": json.dumps(geometry),
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": 4326,
                    "spatialRel": "esriSpatialRelIntersects",
                }
            )

        payload = _get(layer.url, params).json()
        if "error" in payload:
            raise RuntimeError(f"{layer.name}: {payload['error']}")

        features = payload.get("features") or []
        yield payload
        print(f"    +{len(features):>5} features (offset {offset})")

        more = payload.get("properties", {}).get("exceededTransferLimit") or payload.get(
            "exceededTransferLimit"
        )
        if not features or (not more and len(features) < page_size):
            return
        offset += len(features)


def query(
    layer: Layer,
    where: str = "1=1",
    out_fields: str = "*",
    geometry: dict[str, Any] | None = None,
    page_size: int = 1000,
    out_sr: int = 4326,
) -> gpd.GeoDataFrame:
    """Pull a layer into a GeoDataFrame in EPSG:4326, following pagination."""
    print(f"  {layer.name}: where={where}")
    frames: list[gpd.GeoDataFrame] = []
    for payload in _pages(layer, where, out_fields, geometry, page_size, out_sr):
        if payload.get("features"):
            frames.append(gpd.GeoDataFrame.from_features(payload["features"], crs=out_sr))

    if not frames:
        print(f"  {layer.name}: EMPTY")
        return gpd.GeoDataFrame(geometry=[], crs=f"EPSG:{out_sr}")

    import pandas as pd

    gdf = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), crs=f"EPSG:{out_sr}"
    )
    print(f"  {layer.name}: {len(gdf)} features")
    return gdf


def cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.gpkg"


def write_cache(gdf: gpd.GeoDataFrame, name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(name)
    gdf.to_file(path, driver="GPKG", layer=name)
    return path


def read_cache(name: str) -> gpd.GeoDataFrame:
    path = cache_path(name)
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run fetch.py first")
    return gpd.read_file(path, layer=name)
