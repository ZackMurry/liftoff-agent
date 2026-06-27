import { supabase } from "./supabase";

// --- Webhook Events ---

export async function insertWebhookEvent(params: {
  githubEvent: string;
  action: string | null;
  deliveryId: string | null;
  payload: unknown;
}) {
  const { error } = await supabase.from("webhook_events").insert({
    github_event: params.githubEvent,
    action: params.action,
    delivery_id: params.deliveryId,
    payload: params.payload,
  });
  if (error) throw error;
}

// --- Pull Requests ---

export async function upsertPullRequest(params: {
  githubPrId: number;
  owner: string;
  repo: string;
  title: string;
  branch: string;
  author: string;
  headSha: string;
  diffLength?: number;
}) {
  const { data, error } = await supabase
    .from("pull_requests")
    .upsert(
      {
        github_pr_id: params.githubPrId,
        owner: params.owner,
        repo: params.repo,
        title: params.title,
        branch: params.branch,
        author: params.author,
        head_sha: params.headSha,
        diff_length: params.diffLength,
        status: "active",
        recommendation: "forming",
        updated_at: new Date().toISOString(),
      },
      { onConflict: "owner,repo,github_pr_id,head_sha" },
    )
    .select("id")
    .single();
  if (error) throw error;
  return data.id as string;
}

export async function updatePullRequestStatus(
  prDbId: string,
  status: string,
  recommendation: string,
  reviewBody?: string,
) {
  const { error } = await supabase
    .from("pull_requests")
    .update({
      status,
      recommendation,
      review_body: reviewBody,
      updated_at: new Date().toISOString(),
    })
    .eq("id", prDbId);
  if (error) throw error;
}

// --- Experiments ---

export async function insertExperiment(params: {
  pullRequestId: string;
  scenario: string;
  params: unknown;
}) {
  const { data, error } = await supabase
    .from("experiments")
    .insert({
      pull_request_id: params.pullRequestId,
      scenario: params.scenario,
      params: params.params,
      status: "running",
    })
    .select("id")
    .single();
  if (error) throw error;
  return data.id as string;
}

export async function completeExperiment(
  experimentId: string,
  result: Record<string, unknown>,
) {
  const { error } = await supabase
    .from("experiments")
    .update({
      status: (result.status as string) ?? "error",
      verdict: (result.verdict as string) ?? null,
      error: (result.error as string) ?? null,
      result,
      pass_criteria: result.pass_criteria ?? null,
      finished_at: new Date().toISOString(),
    })
    .eq("id", experimentId);
  if (error) throw error;
}

export async function updateExperimentLogs(
  experimentId: string,
  logs: string[],
) {
  const { error } = await supabase
    .from("experiments")
    .update({
      result: { logs },
    })
    .eq("id", experimentId);
  if (error) throw error;
}

// --- Dashboard Queries ---

export async function getDashboardPRs() {
  const { data, error } = await supabase
    .from("pull_requests")
    .select("*, experiments(id, scenario, status, verdict, started_at, finished_at)")
    .order("created_at", { ascending: false })
    .limit(50);
  if (error) throw error;
  return data;
}

export async function getPRWithExperiments(prDbId: string) {
  const { data, error } = await supabase
    .from("pull_requests")
    .select("*, experiments(*)")
    .eq("id", prDbId)
    .single();
  if (error) throw error;
  return data;
}
