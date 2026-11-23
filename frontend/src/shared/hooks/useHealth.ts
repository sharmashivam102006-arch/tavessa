import { useQuery } from "@tanstack/react-query";

import { api } from "@/shared/api/client";
import type { HealthResponse } from "@/shared/api/types";

/** Backend availability, via TanStack Query.
 * Retried once; a failure means "backend unreachable" in the UI —
 * never a stack trace or a raw error to the user. */
export function useHealth() {
  return useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: () => api.get<HealthResponse>("/api/v1/health"),
    retry: 1,
    staleTime: 30_000,
  });
}
