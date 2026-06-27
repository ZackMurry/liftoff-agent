import { NextRequest, NextResponse } from "next/server";
import {
  getInstallationAccessToken,
  verifyWebhookSignature,
  getInstallationOctokit,
  getPRDiff,
} from "@/lib/github";
import { runAgent } from "@/lib/agent";

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

  if (!verifyWebhookSignature(payload, signature, secret)) {
    return NextResponse.json({ error: "Invalid signature" }, { status: 401 });
  }

  const event = req.headers.get("x-github-event");
  if (event !== "pull_request") {
    return NextResponse.json({ ok: true, skipped: true });
  }

  const body = JSON.parse(payload);
  const action: string = body.action;

  if (action !== "opened" && action !== "synchronize") {
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

  // Fire-and-forget: respond 200 immediately, process in background.
  // waitUntil keeps the serverless function alive for the background work.
  const promise = (async () => {
    try {
      const octokit = await getInstallationOctokit(installationId);
      const source = {
        ...sourceBase,
        token: await getInstallationAccessToken(installationId),
      };
      const diff = await getPRDiff(octokit, owner, repo, pullNumber);
      await runAgent({ diff, prTitle, prBody, octokit, owner, repo, pullNumber, source });
    } catch (err) {
      console.error("Agent error:", err);
    }
  })();

  // Vercel's waitUntil (available on Edge and Node runtimes)
  if (typeof globalThis !== "undefined" && "waitUntil" in globalThis) {
    (globalThis as any).waitUntil(promise);
  }

  return NextResponse.json({ ok: true });
}
