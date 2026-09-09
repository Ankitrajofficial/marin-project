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
  ('imd_cyclone',       'IMD Cyclone Bulletin',     'scrape', 0.95,
   'https://mausam.imd.gov.in/responsive/cycloneinformation.php',
   'Tier 3. No API, bulletin scrape. Authoritative -> high prior.')
ON CONFLICT (source_id) DO NOTHING;
