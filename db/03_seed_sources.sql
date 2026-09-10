-- ORCA step 1: source registry seed.
-- reliability is the fusion prior (see COMMENT ON sources.reliability).
-- Rationale for the ordering: authoritative human-issued bulletins (INCOIS,
-- IMD) outrank observed-transmission data (AIS), which outranks modelled
-- reanalysis (Copernicus), which outranks free forecast APIs (Open-Meteo).
INSERT INTO sources (source_id, name, access_mode, reliability, base_url, notes) VALUES
  ('open_meteo_marine', 'Open-Meteo Marine',        'api',    0.75,
   'https://marine-api.open-meteo.com/v1/marine',
   'Tier 1. No key. Wave height/period/direction. Primary MVP driver.'),
  ('open_meteo_wx',     'Open-Meteo Weather',       'api',    0.75,
   'https://api.open-meteo.com/v1/forecast',
   'Tier 1. No key. Wind speed/gust/direction.'),
  ('osm_harbours',      'OSM Harbours',             'api',    0.75,
   'https://overpass-api.de/api/interpreter',
   'Tier 1. No key. Harbour/marina/fishing-harbour locations. NOTE: OSM '
   'carries no usable-depth tag for any harbour in the AOI, so draft '
   'compatibility is UNKNOWN, never assumed.'),
  ('aisstream',         'AISStream',                'api',    0.90,
   'wss://stream.aisstream.io/v0/stream',
   'Tier 1 but needs a free key. Live AIS vessel positions via websocket.'),
  ('overpass',          'OSM Overpass',             'api',    0.80,
   'https://overpass-api.de/api/interpreter',
   'Tier 1. No key. Harbour/port geometry for the safe-harbour set.'),
  ('copernicus',        'Copernicus Marine',        'api',    0.90,
   'https://data.marine.copernicus.eu',
   'Tier 2. Registration. SST/CHL/currents; the gapfill.py input.'),
  ('incois_pfz',        'INCOIS PFZ Advisory',      'scrape', 0.95,
   'https://incois.gov.in/portal/osf/pfzAdvisory.jsp',
   'Tier 3. No API, bulletin scrape. Authoritative -> high prior.'),
  ('marine_regions',    'MarineRegions (VLIZ)',     'api',    0.80,
   'https://geo.vliz.be/geoserver/MarineRegions/wfs',
   'Tier 1. No key, open WFS. EEZ / territorial sea / treaty boundary lines '
   'incl. the India-Sri Lanka IMBL. CC-BY 4.0. ADVISORY ONLY: a scientific '
   'compilation, NOT Survey of India, no legal authority.'),
  ('incois_lc',         'INCOIS landing centres',   'api',    0.95,
   'https://incois.gov.in/geoserver/PFZ_LandingCentres/wfs',
   'Tier 2.5, VERIFIED via workspace-scoped WFS (top-level WFS is 403). '
   'Official INCOIS fishing landing centres -- the places small craft '
   'actually shelter, unlike OSM ports and marinas. SNAPSHOT frozen at '
   '2024-04-27 (layer is named LandingCenters_29Apr2024): locations only, '
   'surfaced with that date as the data age. The PFZ advisory fields in the '
   'same rows are 2+ years stale and stored as provenance only. '
   'DEPTH_FROM/DEPTH_TO are FISHING ZONE depths offshore, NOT harbour depths, '
   'and are deliberately not loaded into depth_m.'),
  ('imd_cap',           'IMD CAP alerts',           'api',    0.98,
   'https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml',
   'Tier 1, VERIFIED REACHABLE with no credentials. Official CAP 1.2 warnings '
   'from India Meteorological Department, NWFC Division, New Delhi. Public '
   'domain per the feed. Highest reliability of any source here: it is the '
   'legally mandated national warning authority, not a model. ATTRIBUTION IS '
   'MANDATORY and enforced by hazard_zones.attribution NOT NULL. '
   'NOTE: api.imd.gov.in/api/v1/* (cyclone_track, cyclone_wind, cyclone_cou, '
   'seabulletin, coastalbulletin) all return HTTP 401 "API key missing" -- '
   'documented but not open. The x-api-key header is recognised.'),
  ('scenario_sim',      'Scenario simulation',      'api',    0.90,
   NULL,
   'SYNTHETIC. Injected by backend/app/scenarios/ for demonstration. '
   'Reliability 0.90, NOT 0.0: reliability answers how much we believe a '
   'source about the real world, and a scenario REPLACES the real world -- at '
   '0.0 the weighted fusion would give it no weight and the injected cyclone '
   'would be invisible. Visibility comes from this source_id, which flags '
   'simulated=true on every risk cell, recall entry and trace derived from it.'),
  ('imd_cyclone',       'IMD Cyclone Bulletin',     'scrape', 0.95,
   'https://mausam.imd.gov.in/responsive/cycloneinformation.php',
   'Tier 3. No API, bulletin scrape. Authoritative -> high prior.')
ON CONFLICT (source_id) DO NOTHING;
