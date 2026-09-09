// Mirrors backend/app/api/schemas.py. `simulated` is non-optional on purpose:
// TypeScript will not let a component render a hazard number without having
// the flag in hand.

export interface RiskProperties {
  h3_cell: string;
  hazard_prob: number | null;   // null = NO DATA. Not zero risk.
  uncertainty: number;
  simulated: boolean;
  simulated_sources: string[];
  drivers: Drivers;
}

export interface Drivers {
  hazard_prob?: number;
  combine_rule?: string;
  combine_note?: string;
  hazard_prob_if_fully_correlated?: number;
  partial_coverage?: boolean;
  coverage?: string[];
  missing?: string[];
  lower_bound?: boolean;
  contributions?: Record<string, number>;
  variables?: Record<string, VariableDriver>;
  uncertainty_terms?: Record<string, number>;
  simulated?: boolean;
  simulated_sources?: string[];
  no_coverage?: boolean;
  note?: string;
}

export interface VariableDriver {
  mu: number; unit: string; threshold: number; p_exceed: number;
  sigma: number; sigma_base: number; sigma_disagreement: number;
  sigma_basis: string; n_sources: number; sources: string[];
  origin: "self" | "neighbour"; lead_hours: number;
  weighting: string; has_fetch_proxy: boolean; simulated: boolean;
}

export interface RiskFeature {
  type: "Feature";
  geometry: { type: "Polygon"; coordinates: number[][][] };
  properties: RiskProperties;
}

export interface RiskFeatureCollection {
  type: "FeatureCollection";
  valid_time: string;
  requested_time: string | null;
  simulated: boolean;
  n_features: number;
  hazard_prob_max: number | null;
  features: RiskFeature[];
}

export interface RiskTimes {
  times: string[]; count: number; first: string | null; last: string | null;
}

export interface TraceObservation {
  source_id: string; variable: string; value: number; unit: string;
  valid_time: string; issued_time: string | null; issued_time_kind: string | null;
  age_seconds: number | null;
  age_is_lower_bound: boolean;
  origin: "self" | "neighbour";
  from_cell: string; reliability: number; simulated: boolean;
}

export interface CellTrace {
  h3_cell: string; valid_time: string; requested_time: string | null;
  hazard_prob: number | null; uncertainty: number;
  simulated: boolean; simulated_sources: string[];
  drivers: Drivers; observations: TraceObservation[];
}
