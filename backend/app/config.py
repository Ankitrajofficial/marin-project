"""Configuration. Env-backed settings plus the few constants that must be
identical everywhere in the codebase.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# H3 resolution. THE single definition -- core/grid.py imports this rather
# than declaring its own, so there is exactly one.
#
# Deliberately NOT env-tunable: h3_cell values are persisted, so changing
# resolution invalidates every stored observation and risk cell. It is a
# migration, not a setting.
#
# Why 5: both Tier-1 marine sources are ~9 km native (Open-Meteo Marine 0.08
# deg, Copernicus 1/12 deg). Res 5 is ~8.5 km edge / ~252 km^2 per cell, which
# matches the data we actually have. Res 6 (~3.2 km edge) would oversample it
# ~7x and store interpolated values as if they were observations -- over a
# 2.3M km^2 EEZ that is ~13.8M rows/day vs ~2M at res 5.
#
# Routing may later want a finer graph near the coast. That is a second, local
# grid inside core/routing.py -- not a global bump here.
H3_RESOLUTION = 5


# Source ids that carry SIMULATED data rather than observations of the real
# world. Anything computed from one of these is flagged simulated all the way
# out to the API, so it can never be presented as a real forecast.
#
# A frozenset in config rather than a literal in core/ because the guard has to
# be checked in several places and one of them silently disagreeing is exactly
# the failure this prevents.
SCENARIO_SOURCE_IDS: frozenset[str] = frozenset({"scenario_sim"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://orca:orca@localhost:5432/orca"

    db_pool_min: int = 1
    db_pool_max: int = 10

    log_level: str = "INFO"

    # Tier 2 sources (registration required). Optional: absence must not stop
    # the app booting, it only stops the adapters that need them.
    aisstream_api_key: str | None = None
    copernicus_username: str | None = None
    copernicus_password: str | None = None
    bhashini_api_key: str | None = None


settings = Settings()
