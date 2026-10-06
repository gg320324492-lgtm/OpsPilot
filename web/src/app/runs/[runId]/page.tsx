import { Placeholder } from "@/components/placeholder";

/**
 * Run Detail. A dynamic route rather than a query parameter because the run id
 * is the resource: `/runs/{run_id}` mirrors `GET /api/runs/{run_id}`, so the URL
 * an operator can paste into a colleague's chat identifies the same thing the
 * API call does.
 */
export default function RunDetailPage() {
  return <Placeholder title="Run Detail" />;
}