import { Dashboard } from "@/components/Dashboard";

/**
 * Dashboard route (product-spec 画面5, admin/demo). Thin client-screen wrapper:
 * the `Dashboard` component loads GET /dashboard and renders the aggregate view.
 */
export default function DashboardPage() {
  return <Dashboard />;
}
