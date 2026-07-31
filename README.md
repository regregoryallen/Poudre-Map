# Poudre-Map

Maps of the Cache la Poudre River watershed in northern Colorado — an
interactive web viewer for exploring the basin, and a static renderer for
producing a finished print map. Both are driven by the same data pipeline.

## The basin

USGS Watershed Boundary Dataset HUC8 `10190007`, "Cache La Poudre" —
4,896 km² (1,890 mi²) draining to the South Platte near Greeley.

The WBD lists its states as **CO, WY**. The North Fork headwaters reach above
41°N into Wyoming, so this is not a Colorado-only basin and must not be clipped
to the state. An independent NLDI delineation upstream of the Greeley gage puts
the north edge at 41.21°N, confirming it.

Worth noting on any finished map: Horsetooth Reservoir and Carter Lake sit
inside the basin but are Colorado-Big Thompson Project features importing water
*from the Colorado River basin*. The polygon is not a hydrologically
self-contained system.

## Approach

Three stages. The first two are shared; only rendering diverges.

| Stage | Does | Output |
|---|---|---|
| `fetch.py` | Pulls each source's REST endpoint. Only stage that touches the network. | `data/cache/*.gpkg` |
| `build.py` | Reprojects, clips, dissolves, simplifies per detail tier. Deterministic. | `data/derived/*.geojson` (EPSG:4326) |
| `terrain.py` | 3DEP → hillshade. Full-res for print, downsampled for web. | GeoTIFF |
| `tiles.py` | `tippecanoe` → single static vector-tile file. | `poudre.pmtiles` |
| `render.py` | Static cartography in EPSG:26913. | PNG / PDF / SVG |

Canonical data is stored in EPSG:4326; each renderer reprojects at the end —
the web path to Web Mercator (forced), the print path to NAD83 / UTM 13N, which
keeps the basin correctly shaped.

`config/style.yml` feeds both renderers so the two outputs read as siblings
rather than unrelated maps.

## Build order

1. `fetch.py` + `build.py` — most of the real work, and where the risk is:
   reconciling live WBD against the CSU layers and confirming the Wyoming edge
   survives every clip and join.
2. `terrain.py` — independent once the extent is fixed.
3. `tiles.py` + `web/` — fast once the data is clean.
4. `render.py` — last, informed by what the web viewer reveals.

## Data

See `config/sources.yml` for pinned endpoints, the snapshot date, and
per-source attribution. Nothing under `data/` is tracked; it is regenerable.

Attribution requirements vary by source and must be composed from whichever are
actually in use. USGS products (WBD, NHD, 3DEP, NLDI) are public domain. Esri
and OpenStreetMap basemaps both require visible attribution.
