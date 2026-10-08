import { useQuery } from "@tanstack/react-query";
import { api, getProject } from "./api";

/** What the signed-in person may do in the current project (GET /api/projects/members/me/permissions, Hamsa's shape:
 *  resource → action → allowed). While it loads, or when it can't be read, everything is allowed: the API refuses what
 *  isn't, so this only hides or disables buttons. Example: can("agents", "deploy"). */
export function usePermissions() {
  const project = getProject() ?? "";
  const q = useQuery({ queryKey: ["permissions", project], queryFn: () => api.myPermissions(project), staleTime: 60_000, retry: false });
  const d = q.data?.data;
  return {
    loaded: !!d, role: d?.role ?? null, agents: d?.agents ?? null,
    can: (resource: string, action: string) => d?.resolvedPermissions?.[resource]?.[action] ?? true,
  };
}
