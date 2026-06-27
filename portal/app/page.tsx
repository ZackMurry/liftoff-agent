import { getDashboardPRs } from "@/lib/db";
import Dashboard, { PullRequestRow, DashboardCounts } from "./dashboard";

export const revalidate = 5;

type DotState = "pass" | "fail" | "run" | "queue";

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return "yesterday";
  return `${days}d ago`;
}

function mapPrRow(dbRow: Record<string, unknown>): PullRequestRow {
  const experiments = (dbRow.experiments as Array<Record<string, unknown>>) ?? [];
  const dots: DotState[] = experiments.map((e) => {
    if (e.status === "passed") return "pass";
    if (e.status === "failed") return "fail";
    if (e.status === "running") return "run";
    return "queue";
  });

  return {
    id: String(dbRow.github_pr_id),
    title: dbRow.title as string,
    branch: dbRow.branch as string,
    author: dbRow.author as string,
    status: (dbRow.status as PullRequestRow["status"]) ?? "active",
    rec: (dbRow.recommendation as PullRequestRow["rec"]) ?? "forming",
    dots,
    time: relativeTime(dbRow.updated_at as string),
  };
}

export default async function Home() {
  const rows = await getDashboardPRs();
  const prs = rows.map(mapPrRow);
  const counts: DashboardCounts = {
    all: prs.length,
    active: prs.filter((p) => p.status === "active").length,
    failed: prs.filter((p) => p.status === "failed").length,
    passed: prs.filter((p) => p.status === "passed").length,
    merged: prs.filter((p) => p.status === "merged").length,
  };

  return <Dashboard prs={prs} counts={counts} />;
}
