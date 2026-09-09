# DATA-SOURCES.md

Endpoint discovery for ORCA's real-data integration. Every schema below was recorded by
actually calling the endpoint on **2026-09-10** and reading the real response. Nothing here is
guessed.

---

## Summary table

| Source | Reachable | Data quality | Recommendation |
|---|---|---|---|
| Open-Meteo Marine | **Yes**, fast (<1s) | Live hourly, 72h horizon | **Keep as primary** sea state |
| Open-Meteo Forecast | **Yes**, fast (<1s) | Live hourly, 72h horizon | **Keep as primary** wind/rain |
| Open-Meteo Archive | **Yes** | ERA5 reanalysis to ~5 days ago | Keep for cyclone replay |
| **INCOIS GeoServer WFS** | **Yes**, fast, occasional 503 | **LIVE PFZ advisory lines, today's date** | **★ USE THIS. Replaces the derived PFZ layer entirely** |
| INCOIS GeoServer WMS | Yes | 232 layers incl. SST/chl imagery | Use as map layers |
| INCOIS ERDDAP | Yes, fast | **All 17 datasets are historical.** Newest ends 2023-05-21 | **Not a live source.** Use for model validation only |
| INCOIS PFZ HTML pages | Yes | Static description pages, no advisory content | **Do not scrape.** Use the GeoServer WFS instead |
| GDACS event list | Yes, slow (~8s) | Live TC events, GeoJSON | Use for cyclone feed |
| GDACS getgeometry | Yes (~1s) | Track, forecast cone, wind radii | Use for track + cone |
| GDACS geteventdata | **Timed out at 60s** | n/a | Skip. List + geometry are sufficient |
| GDACS RSS | Yes | 393 items, rich `gdacs:*` fields | Optional severity/population enrichment |
| GEBCO WMS | Yes, but **first call timed out at 60s** | 15 arc-sec global grid | Use with long timeout + retry |
| ODB GEBCO API | **Yes**, fast (0.5–3.5s) | Point, transect, area, batch | **Use for depth queries** |
| NOAA MUR SST (`jplMURSST41`) | Yes | **Live**, 1 km, to 2026-09-08 | Use as SST cross-check / fallback |
| NOAA VIIRS chlorophyll | Yes | **Cloud-limited: 0% valid off Chennai today** | Fallback only, must handle empty |
| `coastwatch.pfeg.noaa.gov` | **No** — connection refused | n/a | Use `upwell.pfeg.noaa.gov` or `coastwatch.noaa.gov` |

### The three findings that change the build

1. **INCOIS publishes live PFZ advisory geometry via an open WFS.** `PFZ_Automation:pfzlines`
   returned 47 MultiLineString features stamped `Year: 2026, Julian_day: "252"` = **2026-09-09**,
   i.e. the current advisory. This is the real thing, as GeoJSON, no key, no scraping. The
   prototype's derived PFZ layer is now a *fallback*, not the primary.

2. **INCOIS ERDDAP contains no live data at all.** All 17 datasets are archives; the newest
   gridded ocean product ends 2023-05-21 and the chlorophyll datasets end 2006 and 2020. It is
   valuable for validating the derived-PFZ model against history, and useless for today.

3. **Satellite chlorophyll over the Bay of Bengal is unusable on most days.** Measured
   cloud-free fraction off Chennai: **0.0%** on 2026-08-12, **0.0%** on 2026-03-10, 70.2% on
   2026-02-15, 46.6% on 2026-01-20. This is not a query bug — it is why INCOIS issues advisories
   three times a week and skips sectors. Any chlorophyll-dependent path must treat "no data" as
   the normal case.

---

## 1. Open-Meteo Marine

**Endpoint** `https://marine-api.open-meteo.com/v1/marine`
**Auth** none · **CORS** permissive, browser-callable · **Cadence** hourly

```
GET https://marine-api.open-meteo.com/v1/marine
  ?latitude=13.08&longitude=80.29
  &hourly=wave_height,wave_direction,wave_period,swell_wave_height,sea_surface_temperature
  &timezone=Asia%2FKolkata&forecast_days=1
```

Real response (2026-09-10):

```json
{
  "latitude": 13.041664, "longitude": 80.375015,
  "generationtime_ms": 0.324,
  "hourly_units": {"time":"iso8601","wave_height":"m","wave_direction":"°",
                   "wave_period":"s","swell_wave_height":"m","sea_surface_temperature":"°C"},
  "hourly": {"time":["2026-09-10T00:00", ...24 steps],
             "wave_height":[0.8, ...], "wave_direction":[145, ...],
             "wave_period":[9.35, ...], "swell_wave_height":[0.78, ...],
             "sea_surface_temperature":[30.5, ...]}
}
```

**Gotcha — coordinate snapping.** Requested `13.08, 80.29`; got back `13.0417, 80.3750`. The
marine grid snaps ~0.085° east, i.e. offshore. Useful (it lands in water) but the returned
`latitude`/`longitude` must be displayed, not the requested ones.

`forecast_days` max 3 for the free tier at hourly resolution. Archive mode: add
`start_date`/`end_date` instead of `forecast_days`.

## 2. Open-Meteo Forecast

**Endpoint** `https://api.open-meteo.com/v1/forecast` · **Auth** none

```
GET .../v1/forecast?latitude=13.08&longitude=80.29
  &hourly=wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation,weather_code
  &timezone=Asia%2FKolkata&forecast_days=1&wind_speed_unit=kmh
```

Real response: `latitude 13.0404, longitude 80.2548`, units
`{"wind_speed_10m":"km/h","wind_gusts_10m":"km/h","wind_direction_10m":"°",
"precipitation":"mm","weather_code":"wmo code"}`, first step
`wind_speed_10m=13.9, wind_gusts_10m=29.9, wind_direction_10m=222, precipitation=0, weather_code=1`.

**Multi-point mode (verified).** Both Open-Meteo endpoints accept comma-separated coordinate
lists and return a JSON **array**, one object per point. 9 route waypoints = 2 HTTP requests.

## 3. GDACS

### 3a. Event list

```
GET https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH
  ?eventlist=TC&fromdate=2026-06-12&todate=2026-09-10&alertlevel=Green;Orange;Red
```

`HTTP 200, 8.0s, 47 KB.` GeoJSON `FeatureCollection`, 34 features, `geometry.type: "Point"`
(the centroid). Alert distribution: Green 29, Orange 4, Red 1. `iscurrent === "true"`: 4.

Full `properties` schema:

```
eventtype eventid episodeid eventname glide name description htmldescription
icon iconoverall url{geometry,report,details} alertlevel alertscore
episodealertlevel episodealertscore istemporary iscurrent country
fromdate todate datemodified iso3 source sourceid polygonlabel Class
countryonland affectedcountries[{iso2,iso3,countryname}]
severitydata{severity,severitytext,severityunit}
```

**Gotchas, all verified:**
- `iscurrent` and `istemporary` are **strings** `"true"`/`"false"`, not booleans.
- `severitydata.severity` is max sustained wind, `severityunit: "km/h"`.
- `source: "JTWC"` — confirms GDACS is not IMD. See the authority rule in DECISIONS.md.
- **No North Indian Ocean filter parameter exists.** Filter client-side on
  `geometry.coordinates` (lon 40–100, lat −5 to 30). Today that yields **0 of 34** — no active
  NIO cyclone, which is the normal state and must be handled as such.

### 3b. Track, forecast cone and wind radii

```
GET https://www.gdacs.org/gdacsapi/api/polygons/getgeometry
  ?eventtype=TC&eventid=1001318&episodeid=33
```

`HTTP 200, 268 KB, 81 features.` Discriminate by `properties.Class`:

| `Class` pattern | Geometry | Meaning |
|---|---|---|
| `Point_Centroid` | Point | Current storm centre |
| `Point_Polygon_Point_N` | Polygon | Track point radii. `featuretype: "PointRadii"`, `polygonlabel: "01/09 06:00 UTC"`, `key: "09010600"` |
| `Line_Line_N` | LineString | Track segment. **`forecast: false \| true`** separates observed from forecast |
| `Poly_Cones` | Polygon | **The forecast uncertainty cone.** `polygonlabel: "Uncertainty Cones"` |
| `Poly_Red` / `Poly_Orange` / `Poly_Green` | Polygon | Wind radii by intensity. `featuretype: "WindRadii"`, `key` timestamp |

**Gotcha — inconsistent booleans.** `forecast` on `Line_Line_*` is a real boolean, while
`iscurrent` on the event list is a string. Do not share a coercion helper between them.

### 3c. RSS

`https://www.gdacs.org/xml/rss.xml` — `HTTP 200, 1.2 MB, 393 items` across all hazard types
(9 TC). Namespace fields available: `gdacs:alertlevel alertscore severity population eventid
episodeid country iso3 bbox cap resources fromdate datemodified iscurrent temporary
calculationtype durationinweek`. Optional enrichment; the JSON API covers the core need.

### 3d. Event details — DO NOT USE

`https://www.gdacs.org/gdacsapi/api/events/geteventdata?eventtype=TC&eventid=1001318`
**Timed out after 60s, 0 bytes.** Transient or overloaded. The list plus getgeometry supply
everything needed; skip this endpoint.

---

## 4. ODB GEBCO depth API

**Endpoint** `https://api.odb.ntu.edu.tw/gebco` · **Auth** none · **Cache** indefinitely
`https://api.odb.ntu.edu.tw/openapi.json` returns **HTTP 502** — no machine-readable spec.

The `jsonsrc` parameter takes a URL-encoded GeoJSON geometry. **The geometry type changes the
semantics entirely** — this is the most important thing on this page:

| `jsonsrc` type | Behaviour | Verified result |
|---|---|---|
| `Point` | Single depth | `{"longitude":[80.45],"latitude":[13.08],"z":[-53.0],"lineid":[0]}` |
| `MultiPoint` | **Transect** — interpolates along the path | 3 input points → **86 samples** |
| `lon=a,b,c&lat=d,e,f` | **Transect**, same as MultiPoint | 86 samples |
| `MultiLineString` | **N independent lines**, each with its own `lineid` | **← true batch form** |
| `Polygon` | Grid samples over the area | 150 samples, no `lineid` column |

**Batch pattern for N independent points** (verified): send a `MultiLineString` of N degenerate
2-point segments, then take the first sample of each `lineid`.

```
jsonsrc={"type":"MultiLineString","coordinates":[
  [[80.45,13.08],[80.4501,13.0801]],
  [[80.60,13.00],[80.6001,13.0001]],
  [[80.80,12.90],[80.8001,12.9001]]]}
→ lineid 0 → 80.4500 13.0800 depth  -53 m
  lineid 1 → 80.6000 13.0000 depth -157 m
  lineid 2 → 80.8000 12.9000 depth -1250 m
```

Those depths are real and physically sensible: the shelf edge drops away east of Chennai.

`mode=zonly` omits the `distance` column; the default includes it. Depths are **negative
metres** (below sea level). Route depth profiles should use the `MultiPoint` transect
behaviour deliberately — it is exactly the right tool for that job.

## 5. GEBCO WMS

**Endpoint** `https://wms.gebco.net/mapserv` · WMS **1.3.0** · **Auth** none

**Reachability caveat.** The first `GetCapabilities` call **timed out at 60s (HTTP 000)**; TLS
handshake completed, so the server was simply slow. A retry returned 200 in 0.9s. Requires a
long timeout and at least one retry.

Layers:

```
GEBCO_Grid                    This is a WMS for the GEBCO global bathymetric grid
GEBCO_LATEST                  GEBCO Grid shaded relief
GEBCO_LATEST_2                GEBCO Grid colour-shaded for elevation      ← use this one
GEBCO_LATEST_2_sub_ice_topo   colour-shaded incl. under-ice topography
GEBCO_LATEST_3                (unlabelled)
GEBCO_LATEST_TID              Type Identifier grid
GEBCO_LATEST_TID_2            Type Identifier grid
```

**CRS** `EPSG:4326`, `EPSG:3395`, `EPSG:3857` — 3857 confirmed working, which is Leaflet's
default. **Formats** png, jpeg, gif, svg+xml, tiff, `png; mode=8bit`, vnd.jpeg-png.

Verified GetMap (returned a real 512×512 RGBA PNG of the Tamil Nadu shelf):

```
https://wms.gebco.net/mapserv?service=WMS&version=1.3.0&request=GetMap
  &layers=GEBCO_LATEST_2&styles=&format=image/png&transparent=true
  &width=512&height=512&crs=EPSG:3857
  &bbox=8682920.28,893463.75,9573476.21,1804722.77
```

**Two gotchas:**
- WMS 1.3.0 + `EPSG:4326` uses **lat,lon axis order** (`bbox=8,78,16,86` = lat 8–16, lon 78–86).
  `EPSG:3857` is x,y. Getting this wrong yields a blank or wrong-region tile.
- **`GetLegendGraphic` returns XML, not an image** (`HTTP 200, 463 bytes, text/xml`). A depth
  legend must be built in the app, not fetched.

**Licence** GEBCO permits research and non-commercial use without prior permission provided the
source is credited. Credit line required.

---

## 6. INCOIS

### 6a. ERDDAP — historical archive, NOT a live source

`https://erddap.incois.gov.in/erddap/` · reachable, fast (1.2s) · **17 datasets**

Search JSON needs redirect-following and pagination:
`/erddap/search/index.json?page=1&itemsPerPage=1000&searchFor=<term>`
(a bare `?searchFor=` returns **HTTP 302**; `searchFor=fish` returns **HTTP 404** — no match.)

Time coverage measured for every relevant dataset:

| Dataset ID | Variables | Coverage | Live? |
|---|---|---|---|
| `IRS_chlorophyll_datasets` | CHLOROPHYLL | 2003-01-05 → **2006-03-21** | No |
| `incois_oceansat2_datasets` | CHL, KD490, TSM | 2011-02-02 → **2020-05-01** | No |
| `AMSRE_MONTHLY_GLOBAL` | SST, WSPD, VAPOR, CLOUD, RAIN | 2002-06-14 → **2011-09-14** | No |
| `incois_tmi_3day_datasets` | SST, WSPD, VAPOR, CLOUD, RAIN | 1997-12-07 → **2014-12-31** | No |
| `NOAA_AVHRR_AMSR_datasets` | sst, anom | 2002-06-01 → **2011-10-04** | No |
| `ascat_daily_datasets` | wind_speed, u/v, stress | 2007-03-21 → **2023-05-21** | No |
| `incois_argo_sst_weekly` | ASST, ERR | 2009-01-07 → **2010-12-29** | No |
| `incois_valueadded_products_datasets` | MLD, ILD, D26, D20, HTCNT | 2004-01-10 → **2019-03-30** | No |
| `Indian_ARGO_Floats` | float profiles | 2002-10-24 → **2025-04-23** | No |

**Conclusion: zero live datasets.** Use ERDDAP for historical validation of the derived-PFZ
model — which is a genuinely defensible use — and never as a current-conditions source.

### 6b. GeoServer — the real find

`https://incois.gov.in/geoserver/` runs a **public GeoServer with WMS *and* open WFS**,
discovered from the geoportal at `/geoportal/MFASPFZ/index.html`.

Global capabilities: `/geoserver/wms?service=WMS&version=1.3.0&request=GetCapabilities`
→ `HTTP 200, 446 KB, **232 named layers** across 50 workspaces.`

**WFS returns GeoJSON.** Verified pattern:

```
GET https://incois.gov.in/geoserver/<workspace>/ows
  ?service=WFS&version=1.1.0&request=GetFeature
  &typeName=<workspace>:<layer>&outputFormat=application/json
  [&propertyName=a,b,c][&maxFeatures=N][&CQL_FILTER=...]
```

`CQL_FILTER` is supported (verified: `Julian_day='252'` → `numberMatched: 47`).

#### `PFZ_Automation:pfzlines` — LIVE PFZ ADVISORY

`totalFeatures: 47` · `MultiLineString` · `EPSG:4326`

```json
"properties": {
  "Category": "ghrsst", "SECTORBOUN": "", "SECTORBO_1": "", "SECTORNAME": "",
  "Julian_day": "252", "Sno": "001", "Year": 2026,
  "UID": 2026252001, "Length": 58.35886
}
```

**All 47 features are `Year: 2026, Julian_day: "252"` = 2026-09-09 — the current advisory.**
`Category: "ghrsst"` names the satellite SST product behind it. `UID` = `YYYYDDDNNN`.

**Gotcha:** `SECTORNAME` is **blank** on this layer. Derive the sector by spatial join against
`PFZ_Sectors:sector_new`.
**Gotcha:** passing `propertyName` without a geometry column returns `"geometry": null`.

#### `PFZ_Sectors:sector_new` — the 14 official sectors

`totalFeatures: 14` · `MultiPolygon` · properties
`SDE_SECTOR PERIMETER SBOUND_ SBOUND_ID SECTORNAME SEC_ID SHAPE_AREA SHAPE_LEN`
Example: `{"SECTORNAME":"GUJARAT","SEC_ID":"SEC001", ...}`

This is the authoritative sector geometry. **Bundle this instead of hand-drawing sectors.**

Sector IDs cross-checked against the `alt` text of the image map on
`/MarineFisheries/TextDataHome?mfid=1`:

```
SEC001 Gujarat        SEC002 Maharashtra   SEC003 Goa
SEC004 Karnataka      SEC005 Kerala        SEC006 South TamilNadu
SEC007 North TamilNadu SEC008 South Andhra Pradesh
SEC009 North Andhra Pradesh                SEC010 Orissa
SEC011 West Bengal    SEC012 Andaman       SEC013 (Nicobar)
SEC014 Lakshadweep
```

#### `PFZ_LandingCentres:LandingCenters_29Apr2024` — 1,223 landing centres

`Point` geometry. 28 properties including the full advisory structure:

```
OBJECTID SECTOR_NAM SECTOR_ID DIST_NAME LC_NAME LC_UNIQUE_ LONGITUDE LATITUDE
FORECAST_I UPDATED_DA FORECAST_D VALIDITY_D DIRECTION BEARING
DISTANCE_F DISTANCE_T DEPTH_FROM DEPTH_TO STATUS MARINE_FIS  (+ DMS breakdowns)
```

Real row:
```json
{"SECTOR_ID":"SEC002","LC_NAME":"Satpati","FORECAST_D":"2024-04-27T18:30:00Z",
 "VALIDITY_D":"2024-04-28T18:30:00Z","BEARING":292,"DISTANCE_T":26,
 "DEPTH_FROM":18,"DEPTH_TO":23,"STATUS":"YES"}
```

**This layer is a static 2024-04-27 snapshot** — every sampled `FORECAST_D` is identical. Use
it for the 1,223 real landing-centre **positions** (stable reference data) and as the
documented shape of an INCOIS advisory. **Do not present its `BEARING`/`DEPTH` values as
current advice.**

`STATUS` takes `"YES"` / `"NO"` — INCOIS's own encoding of "advisory present for this centre".
299 of 400 sampled rows carried bearing/depth on that date, i.e. ~75% coverage. This is the
"no advisory for your sector today" state, straight from the source.

#### Other useful layers on the same server

| Layer | Contents |
|---|---|
| `TUNA_Automation:tunalines` | **Live** tuna PFZ, `Year 2026 Julian_day 252`, and `State_Name` **is populated** (e.g. `"NORTH ANDHRAPRADESH"`) |
| `PFZ_EEZ:indiaeez` | 19 MultiLineStrings — official EEZ |
| `PFZ_Bathymetry:bathymetry` | Bathymetry |
| `PFZ_OceanColours:Oceancolours` | Ocean colour |
| `PFZ-TUNA-SST-CHL:sst`, `:chl` | SST and chlorophyll raster layers (WMS) |
| `MHW:CORAL_REEF_DISS`, `MANGROVE_ZONE_DISS`, `SEAGRASS_ZONE_DISS`, `TURTLENEST_DISS` | Real ecologically sensitive zones — better than the prototype's 5 hand-drawn rectangles |
| `ABIS:HABSectors` + `ABIS-SECTORS-*` | Harmful algal bloom sectors, colour-coded Normal/Watch/Warning |
| `TideGauges:TideGauges`, `Insitu_TideGauges_Tsunami:TSUNAMIBUOYS` | Tide gauge and tsunami buoy positions |
| `OSF_CoastalForecast:SECTORNAME_*` | Ocean state forecast sectors, 12 states |
| `EnergyAtlas:Ports_Harbours` | Ports and harbours |

**Reliability.** One request returned **HTTP 503 "Service Unavailable"** mid-session; the very
next retry returned 200. Treat 503 as transient: retry once after a short backoff, then fall
back. Never let it block a demo.

**CORS.** Not verified as browser-callable. Route all GeoServer calls through a Next.js server
route handler.

### 6c. PFZ HTML pages — do not scrape

| URL | Result |
|---|---|
| `/MarineFisheries/TextDataHome?mfid=1&request_locale=en` | 200, 50 KB. Contains only an HTML image map linking to `TextData?secid=SEC001..SEC014` |
| `/MarineFisheries/TextData?secid=SEC003` | 200, 35 KB. **Background prose only** — no advisory content, no tables, no `<select>` |
| `/MarineFisheries/PfzAdvisory` | 200, 38 KB. Static description page |

No AJAX endpoint exists behind these pages: zero inline scripts referencing ajax/fetch/secid,
and 0 `<table>` elements. **The advisory content is not in the HTML.** The machine-readable
route is the GeoServer WFS above. This avoids a brittle scraper entirely.

Useful confirmation found in the page prose: *"The PFZ Text provides the information on
location (latitude, longitude), the depth at PFZ location and the distance a[nd bearing]"* —
matching the `LandingCenters` schema exactly.

Also noted: `las.incois.gov.in` (Live Access Server) referenced from the site nav; not probed.

---

## 7. NOAA CoastWatch / ERDDAP

**Host reachability measured:**

| Host | Result |
|---|---|
| `coastwatch.pfeg.noaa.gov` | **Connection refused** after 75s |
| `upwell.pfeg.noaa.gov` | **200** ← use this for classic `erd*` datasets |
| `coastwatch.noaa.gov` | **200** ← use this for global `noaacw*` datasets |
| `polarwatch.noaa.gov` | 200 |
| `erddap.aoml.noaa.gov/hdb` | 200 |
| `www.ncei.noaa.gov` | 200 |
| `oceanwatch.pifsc.noaa.gov` | Connection refused |

### SST — `jplMURSST41` (live, recommended)

`https://upwell.pfeg.noaa.gov/erddap/info/jplMURSST41/index.json`
MUR SST fv04.1, NASA JPL, global **1 km**, coverage 2002-06-01 → **2026-09-08** (live, ~2 day
lag). Variables `analysed_sst, analysis_error, mask, sea_ice_fraction`.
Finer than Open-Meteo's marine grid; good independent cross-check.

### Chlorophyll — cloud-limited

`erdMH1chla1day` (MODIS Aqua) ends **2022-07-24** — dead.
All `erdVHN*` / `erdMW*` / `erdMB*` datasets are **regional** (North Pacific, West US) — wrong
ocean.

Global near-real-time options on `coastwatch.noaa.gov`:

| Dataset | Coverage |
|---|---|
| `noaacwNPPVIIRSchlaDaily` | 2025-08-07 → **2026-08-12** (freshest) |
| `noaacwN20VIIRSchlaDaily` | 2021-08-26 → 2026-06-20 |
| `noaacwN20VIIRSchlaWeekly` | 2021-08-21 → 2026-06-15 |
| `noaacwNPPN20VIIRSchlociDaily` | ends 2021-09-02 |

Query form (**the `altitude` dimension is mandatory** — omitting it produced a 404 that
misleadingly complained about the latitude constraint):

```
GET https://coastwatch.noaa.gov/erddap/griddap/noaacwNPPVIIRSchlaDaily.json
  ?chlor_a[(2026-02-15)][(0.0)][(11.0):(14.0)][(79.8):(82.0)]
```

Response is `{"table":{"columnNames":["time","altitude","latitude","longitude","chlor_a"],
"columnUnits":["UTC","m","degrees_north","degrees_east","mg m^-3"],"rows":[...]}}` with `null`
for cloud-masked pixels.

**Measured cloud-free fraction, box 11–14°N / 79.8–82°E:**

| Date | Valid pixels | Cloud-free | chl-a range (mg m⁻³) |
|---|---|---|---|
| 2026-08-12 (latest) | 0 / 4779 | **0.0%** | — |
| 2026-03-10 | 0 / 4779 | **0.0%** | — |
| 2026-02-15 | 3355 / 4779 | 70.2% | 0.05 – 14.35, mean 0.40 |
| 2026-01-20 | 2226 / 4779 | 46.6% | 0.16 – 16.54, mean 0.55 |

The query is correct — February and January return real data. Chlorophyll is simply
unavailable most days in and after the monsoon. **Any code path that requires chlorophyill must
have a defined "no data" answer, and that answer must be shown to the user, not hidden.**

---

## 8. Caching cadence

Derived from the measured update behaviour of each source:

| Source | TTL | Reason |
|---|---|---|
| Open-Meteo marine / forecast | 1 hour | hourly model steps |
| Open-Meteo archive | indefinite | past never changes |
| INCOIS `pfzlines` / `tunalines` | 6 hours, keyed on `Year`+`Julian_day` | advisory issued ~3×/week; re-check for a new Julian day |
| INCOIS `sector_new`, landing centres, EEZ | indefinite / bundle at build | static reference geometry |
| GDACS event list | 30 min | per-advisory, ~6-hourly episodes |
| GDACS getgeometry | 30 min, keyed on `episodeid` | changes only with a new episode |
| GEBCO WMS tiles | indefinite (browser tile cache) | seabed does not change |
| ODB GEBCO depths | indefinite | same |
| NOAA MUR SST | 6 hours | daily product, ~2 day lag |
| NOAA chlorophyll | 6 hours | daily product, often empty |

## 9. CORS and proxying

| Source | Browser-callable | Action |
|---|---|---|
| Open-Meteo (both) | Yes, permissive CORS | Direct from client is fine; ORCA still proxies for caching |
| GEBCO WMS | Yes — image tiles, no CORS needed | Direct Leaflet layer |
| ODB GEBCO API | Not verified | **Proxy** via route handler |
| GDACS | Not verified | **Proxy** |
| INCOIS GeoServer | Not verified | **Proxy** |
| NOAA ERDDAP | Not verified | **Proxy** |

Default to proxying everything except Open-Meteo and GEBCO tiles. It gives caching, timeouts,
retries and provenance stamping in one place, and removes CORS as a demo-day risk.

## 10. Failures recorded (nothing dropped)

| Source | Failure | Class | Route |
|---|---|---|---|
| `coastwatch.pfeg.noaa.gov` | Connection refused, 75s | Host down / blocked | Use `upwell.pfeg.noaa.gov` |
| `oceanwatch.pifsc.noaa.gov` | Connection refused | Host down / blocked | Not needed |
| GDACS `geteventdata` | Timeout at 60s, 0 bytes | Transient / overloaded | Skip; list + geometry suffice |
| GEBCO `GetCapabilities` | Timeout at 60s on first call, 200 on retry | Transient slowness | Long timeout + 1 retry |
| INCOIS GeoServer WFS | HTTP 503, then 200 on immediate retry | Transient | Retry once, then fall back |
| ODB `openapi.json` | HTTP 502 | Endpoint broken | Schema recorded empirically instead |
| INCOIS ERDDAP `searchFor=fish` | HTTP 404 | No matching dataset | Expected; no PFZ in ERDDAP |
| INCOIS ERDDAP bare `searchFor` | HTTP 302 | Needs pagination params | Add `page` + `itemsPerPage`, follow redirects |
| NOAA chlorophyll, no `altitude` | HTTP 404 with a misleading latitude message | Query form | Always include `[(0.0)]` |
| NOAA chlorophyll, current dates | 0 valid pixels | **Real data gap (cloud)** | Must be surfaced to the user |
