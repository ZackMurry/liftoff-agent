import { NextRequest, NextResponse } from "next/server";
import {
  getInstallationAccessToken,
  verifyWebhookSignature,
  getInstallationOctokit,
  getPRDiff,
} from "@/lib/github";
import { runAgent } from "@/lib/agent";
import { insertWebhookEvent, upsertPullRequest } from "@/lib/db";

export async function POST(req: NextRequest) {
  const secret = process.env.GITHUB_WEBHOOK_SECRET;
  if (!secret) {
    return NextResponse.json(
      { error: "Webhook secret not configured" },
      { status: 500 },
    );
  }

  const payload = await req.text();
  const signature = req.headers.get("x-hub-signature-256") ?? "";

  console.log("[webhook] Received event:", req.headers.get("x-github-event"));

  if (!verifyWebhookSignature(payload, signature, secret)) {
    console.log("[webhook] Signature verification failed");
    return NextResponse.json({ error: "Invalid signature" }, { status: 401 });
  }

  const event = req.headers.get("x-github-event");
  const deliveryId = req.headers.get("x-github-delivery");
  const parsedPayload = JSON.parse(payload);

  await insertWebhookEvent({
    githubEvent: event ?? "unknown",
    action: parsedPayload.action ?? null,
    deliveryId,
    payload: parsedPayload,
  });

  if (event !== "pull_request") {
    console.log("[webhook] Skipping non-PR event:", event);
    return NextResponse.json({ ok: true, skipped: true });
  }

  const body = parsedPayload;
  const action: string = body.action;
  console.log("[webhook] PR action:", action);

  if (action !== "opened" && action !== "synchronize") {
    console.log("[webhook] Skipping action:", action);
    return NextResponse.json({ ok: true, skipped: true });
  }

  const installationId: number = body.installation.id;
  const owner: string = body.repository.owner.login;
  const repo: string = body.repository.name;
  const pullNumber: number = body.pull_request.number;
  const prTitle: string = body.pull_request.title;
  const prBody: string = body.pull_request.body ?? "";
  const sourceBase = {
    clone_url: body.pull_request.head.repo.clone_url as string,
    full_name: body.pull_request.head.repo.full_name as string,
    head_ref: body.pull_request.head.ref as string,
    head_sha: body.pull_request.head.sha as string,
  };

  console.log(`[webhook] Processing PR #${pullNumber}: ${prTitle}`);

  // Process the PR: fetch diff, run agent, post review.
  const handlePR = async () => {
    console.log("[webhook] Fetching installation octokit...");
    const octokit = await getInstallationOctokit(installationId);
    const source = {
      ...sourceBase,
      token: await getInstallationAccessToken(installationId),
    };
    console.log("[webhook] Fetching PR diff...");
    const diff = await getPRDiff(octokit, owner, repo, pullNumber);
    console.log("[webhook] Upserting PR in database...");
    const prDbId = await upsertPullRequest({
      githubPrId: pullNumber,
      owner,
      repo,
      title: prTitle,
      branch: body.pull_request.head.ref,
      author: body.pull_request.user.login,
      headSha: body.pull_request.head.sha,
      diffLength: diff.length,
    });
    console.log(`[webhook] PR DB ID: ${prDbId}. Diff length: ${diff.length} chars. Running agent...`);
    await runAgent({ diff, prTitle, prBody, octokit, owner, repo, pullNumber, source, prDbId });
    console.log("[webhook] Agent finished.");
  };

  // In production (Vercel), use waitUntil for fire-and-forget.
  // Locally, await directly so the work actually completes.
  if (typeof globalThis !== "undefined" && "waitUntil" in globalThis) {
    const promise = handlePR().catch((err) => console.error("Agent error:", err));
    (globalThis as any).waitUntil(promise);
    return NextResponse.json({ ok: true });
  }

  // Local dev: await the agent (GitHub allows up to 10s for webhook response,
  // but smee doesn't enforce that, so this is fine for testing).
  try {
    await handlePR();
  } catch (err) {
    console.error("Agent error:", err);
  }
  return NextResponse.json({ ok: true });
}
