/** Shared API types — Stage 1: health only.
 * Business types are generated from the backend OpenAPI schema from
 * Stage 3 onward; hand-written types here must match Pydantic schemas
 * field-for-field and are marked TODO if they ever diverge. */

export interface HealthResponse {
  status: "ok" | "degraded";
  database: "ok" | "unavailable";
  environment: string;
  version: string;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
