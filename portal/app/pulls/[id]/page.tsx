import { notFound } from "next/navigation";
import { getPRWithExperiments } from "@/lib/db";
import RunPage, { RunPageData } from "./run-page";

export const revalidate = 5;
export const dynamic = "force-dynamic";

function toRunPageData(row: Record<string, unknown>): RunPageData {
  const experiments = ((row.experiments as Array<Record<string, unknown>>) ?? [])
    .slice()
    .sort((a, b) => {
      const aTime = new Date((a.started_at as string | undefined) ?? 0).getTime();
      const bTime = new Date((b.started_at as string | undefined) ?? 0).getTime();
      return aTime - bTime;
    });

  return {
    id: String(row.id),
    number: String(row.github_pr_id),
    repository: `${row.owner}/${row.repo}`,
    title: String(row.title),
    branch: String(row.branch),
    author: String(row.author),
    headSha: String(row.head_sha ?? "").slice(0, 7),
    status: String(row.status ?? "active"),
    recommendation: String(row.recommendation ?? "forming"),
    reviewBody: row.review_body ? String(row.review_body) : null,
    diffLength: typeof row.diff_length === "number" ? row.diff_length : null,
    updatedAt: String(row.updated_at),
    experiments: experiments.map((experiment) => ({
      id: String(experiment.id),
      scenario: String(experiment.scenario),
      status: String(experiment.status ?? "queued"),
      verdict: experiment.verdict ? String(experiment.verdict) : null,
      error: experiment.error ? String(experiment.error) : null,
      params: (experiment.params as Record<string, unknown> | null) ?? null,
      result: (experiment.result as Record<string, unknown> | null) ?? null,
      passCriteria: (experiment.pass_criteria as Record<string, unknown> | null) ?? null,
      startedAt: experiment.started_at ? String(experiment.started_at) : null,
      finishedAt: experiment.finished_at ? String(experiment.finished_at) : null,
    })),
  };
}

export default async function PullRunPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const row = await getPRWithExperiments(id).catch(() => null);
  if (!row) notFound();

  return <RunPage data={toRunPageData(row as Record<string, unknown>)} />;
}
