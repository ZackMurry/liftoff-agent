import { getDashboardPRs } from "@/lib/db";
import Dashboard, { PullRequestRow, DashboardCounts } from "./dashboard";

export const revalidate = 5;
export const dynamic = "force-dynamic";

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
  const sortedExperiments = [...experiments].sort((a, b) => {
    const aTime = new Date((a.started_at as string | undefined) ?? 0).getTime();
    const bTime = new Date((b.started_at as string | undefined) ?? 0).getTime();
    return aTime - bTime;
  });
  const dots: DotState[] = experiments.map((e) => {
    if (e.status === "passed") return "pass";
    if (e.status === "failed") return "fail";
    if (e.status === "running") return "run";
    return "queue";
  });

  return {
    id: String(dbRow.id),
    number: String(dbRow.github_pr_id),
    title: dbRow.title as string,
    branch: dbRow.branch as string,
    author: dbRow.author as string,
    status: (dbRow.status as PullRequestRow["status"]) ?? "active",
    rec: (dbRow.recommendation as PullRequestRow["rec"]) ?? "forming",
    dots,
    latestExperimentStatus: sortedExperiments.at(-1)?.status as string | undefined,
    headSha: dbRow.head_sha ? String(dbRow.head_sha).slice(0, 7) : undefined,
    time: relativeTime(dbRow.updated_at as string),
  };
}

function latestRunPerGithubPR(rows: Array<Record<string, unknown>>) {
  const latest = new Map<string, Record<string, unknown>>();
  for (const row of rows) {
    const key = `${row.owner}/${row.repo}#${row.github_pr_id}`;
    const previous = latest.get(key);
    if (!previous) {
      latest.set(key, row);
      continue;
    }
    const rowTime = new Date(row.updated_at as string).getTime();
    const previousTime = new Date(previous.updated_at as string).getTime();
    if (rowTime > previousTime) latest.set(key, row);
  }
  return [...latest.values()];
}

export default async function Home() {
  const rows = await getDashboardPRs();
  const prs = latestRunPerGithubPR(rows).map(mapPrRow);
  const counts: DashboardCounts = {
    all: prs.length,
    active: prs.filter((p) => p.status === "active").length,
    failed: prs.filter((p) => p.status === "failed").length,
    passed: prs.filter((p) => p.status === "passed").length,
    merged: prs.filter((p) => p.status === "merged").length,
  };

  return <Dashboard prs={prs} counts={counts} />;
}
