# Poudre-Map

Maps of the Cache la Poudre River watershed in northern Colorado — an
interactive web viewer for exploring the basin, and a static renderer for
producing a finished print map. Both are driven by the same data pipeline.

## The basin

USGS Watershed Boundary Dataset HUC8 `10190007`, "Cache La Poudre" —
4,896 km² (1,890 mi²) draining to the South Platte near Greeley.

The WBD lists its states as **CO, WY**. The basin's north edge is 41.2102°N —
past the 41st parallel, which is the Colorado/Wyoming border — so this is not a
Colorado-only basin and must not be clipped to the state. Measured split:

| | km² | share |
|---|---:|---:|
| Colorado | 4,581.8 | 93.6% |
| Wyoming | 311.0 | 6.4% |
| total | 4,892.7 | |

Seven HUC12s carry a WY designation and total ~815 km², but most of that area
lies south of the line; 311 km² is the actual Wyoming land. One subwatershed,
`101900070401` Upper Dale Creek, is entirely in Wyoming.

An independent NLDI delineation upstream of the Greeley gage (USGS-06752500)
covers 4,845.1 km² and overlaps the WBD polygon by 98.9%, which is the
cross-check that the boundary is sound.

`build.py` asserts all of this and refuses to write output if the Wyoming
portion goes missing — see *Why the validation exists* below.

Worth noting on any finished map: Horsetooth Reservoir and Carter Lake sit
inside the basin but are Colorado-Big Thompson Project features importing water
*from the Colorado River basin*. The polygon is not a hydrologically
self-contained system.

## Approach

Three stages. The first two are shared; only rendering diverges.

| Stage | Does | Output | Status |
|---|---|---|---|
| `fetch.py` | Pulls each source's REST endpoint. Only stage that touches the network. | `data/cache/*.gpkg` | done |
| `build.py` | Clips to the basin, validates, writes GeoJSON. Deterministic, offline. | `data/derived/*.geojson` (EPSG:4326) | done |
| `qa.py` | Unstyled diagnostic plot. Not cartography — geometry checking. | `out/qa.png` | done |
| `tiles.py` | `tippecanoe` → single static vector-tile file. | `web/poudre.pmtiles` | done |
| `web/index.html` | MapLibre viewer. No build step, no framework. | — | done |
| `terrain.py` | 3DEP → DEM + shading variants, EPSG:26913. | `data/terrain/*.tif` | done |
| `render.py` | Static cartography in EPSG:26913. | PNG / PDF / SVG | todo |

```bash
make venv && make data && make tiles && make serve
```

Current derived output: 1 basin polygon, 10 HUC10s, 53 HUC12s, 13,016 flowlines
(Strahler ≥ 2), 1,870 waterbodies, 344 canals, 6 gages — 7.0 MB as PMTiles.

## Viewer

Deployed to **http://homeweb.lan/poudremap/** via `./deploy.sh`.

Runtime controls: base layer (Esri hillshade, USGS Topo, USGS Imagery, OSM,
none), subdivision tier (basin / HUC10 / HUC12), hydrography toggles, a
Strahler-order threshold for stream density, and the Wyoming overlays. Hover
reads out the subwatershed under the cursor; clicking any feature opens its
attributes.

Two things the viewer does that are worth not breaking:

- **Hit-test layers stay visible at zero opacity.** `queryRenderedFeatures`
  skips layers set to `visibility: none`, so hiding the subwatershed fills
  when you switch tiers would silently kill hover and click.
- **PMTiles needs HTTP range requests.** Caddy handles this; Python's stock
  `http.server` does not, which is why `src/devserve.py` exists. `deploy.sh`
  verifies a 206 response after every deploy.

`deploy.sh` targets a *subdirectory* and guards on it, so its `rsync --delete`
can only prune inside `/usr/share/caddy/poudremap/` and can never touch the
main site.

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

## Why the validation exists

The CSU Geospatial Centroid layers are a genuinely useful curated collection,
but they are Colorado-only. `CLP_streams24k` has 7,661 features and **zero**
north of 41°N — it is Colorado Division of Wildlife data that stops at the
state line. Using it as the flowline source produces a map that looks entirely
plausible while missing the Wyoming headwaters.

That is the shape of every likely failure here: not a crash, but a basin that
still looks like a basin. So the pipeline uses NHDPlus HR for flowlines
(national coverage), treats every CSU layer as CO-only until proven otherwise,
and `build.py` hard-fails on the Wyoming area, the HUC12 count, the specific
WY-designated subwatersheds, and any basin-wide layer with no features north of
41°N. Expected values live in `expectations:` in `config/sources.yml`.

## Terrain

`terrain.py` builds the relief from USGS 3DEP 1/3 arc-second elevation. The six
1-degree tiles covering the padded basin total ~2 GB, but they are proper COGs
(tiled 512×512, with overviews), so the script window-reads only what
intersects the basin over `/vsicurl` and never downloads the rest. Output is
EPSG:26913 at 20 m — an 11×17 print at 300 dpi needs about 34 m, so that has
room to spare.

In-basin elevation runs **1,389–4,132 m: 2,743 m of relief.**

Three shading variants are produced because the basin has two terrains, and
`terrain_compare.py` renders them side by side over both. The comparison
settles it, though not the way the question was posed:

**No shading variant rescues the plains, because there is nothing there to
shade.** East of the canyon mouth the ground is genuinely flat, and single-sun
and multidirectional relief both go to blank grey. That is not a tuning
failure — it is the terrain reporting itself accurately. The plains have to be
carried by a different layer (the canal network already does this well), not by
a better hillshade.

So the choice is only about the mountains and about what the relief has to
coexist with:

| Variant | Use |
|---|---|
| `hillshade` | Strongest contrast. Fights thin overlaid linework. |
| `hillshade_multi` | Softer, holds detail on all aspects. Best under vectors. |
| `hypsometric` | Only variant that gives the plains an identity. Best standalone. |

## Canals are not streams

NHDPlus carries irrigation canals *inside* the flowline network and assigns
them Strahler orders as high as the Poudre's own. Drawn naively, the Larimer
and Weld Canal and the Greeley Number 2 Canal render as seventh-order rivers.

Filtering on `ftype` gets most of the way (336 CanalDitch, 428 Pipeline, 334
Connector), but not all: where a canal runs through a reservoir, NHD codes that
segment 558 ArtificialPath — the same code it uses for a *river* passing
through a lake. `ftype` alone cannot separate the two.

`build.py:classify_flowlines()` resolves it with the only signal available, the
GNIS name, and tags every reach `natural` so the renderers don't re-derive the
rule. Unnamed 558 reaches stay natural; those are overwhelmingly genuine
through-lake connectors. Result: 12,417 natural and 599 artificial, and at
order 7+ only the Cache la Poudre and its North Fork draw as streams.

The artificial reaches aren't discarded — the viewer has a toggle for them.

## Data

See `config/sources.yml` for pinned endpoints, the snapshot date, and
per-source attribution. Nothing under `data/` is tracked; it is regenerable.

One performance note recorded there because it cost an hour: the NHDPlus HR
flowline layer has 84 columns, and requesting `outFields=*` makes paging slow
enough that a fetch looks hung. Naming the seven fields actually used takes a
1,000-feature page to ~4s.

Attribution requirements vary by source and must be composed from whichever are
actually in use. USGS products (WBD, NHD, 3DEP, NLDI) are public domain. Esri
and OpenStreetMap basemaps both require visible attribution.
