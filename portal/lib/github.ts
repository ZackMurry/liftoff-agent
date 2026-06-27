import { App } from "octokit";
import { createHmac, timingSafeEqual } from "crypto";

let _app: InstanceType<typeof App> | null = null;

export function getApp() {
  if (_app) return _app;
  _app = new App({
    appId: process.env.GITHUB_APP_ID!,
    privateKey: process.env.GITHUB_PRIVATE_KEY!.replace(/\\n/g, "\n"),
    webhooks: { secret: process.env.GITHUB_WEBHOOK_SECRET! },
  });
  return _app;
}

export async function getInstallationOctokit(installationId: number) {
  const app = getApp();
  return app.getInstallationOctokit(installationId);
}

export async function getInstallationAccessToken(installationId: number): Promise<string> {
  const app = getApp();
  const auth = await app.octokit.auth({
    type: "installation",
    installationId,
  });
  return (auth as { token: string }).token;
}

export async function getPRDiff(
  octokit: Awaited<ReturnType<typeof getInstallationOctokit>>,
  owner: string,
  repo: string,
  pullNumber: number,
): Promise<string> {
  const { data } = await octokit.request(
    "GET /repos/{owner}/{repo}/pulls/{pull_number}",
    {
      owner,
      repo,
      pull_number: pullNumber,
      headers: { accept: "application/vnd.github.v3.diff" },
    },
  );
  return data as unknown as string;
}

export async function postReviewComment(
  octokit: Awaited<ReturnType<typeof getInstallationOctokit>>,
  owner: string,
  repo: string,
  pullNumber: number,
  body: string,
) {
  await octokit.request(
    "POST /repos/{owner}/{repo}/issues/{issue_number}/comments",
    {
      owner,
      repo,
      issue_number: pullNumber,
      body,
    },
  );
}

export function verifyWebhookSignature(
  payload: string,
  signature: string,
  secret: string,
): boolean {
  const expected = `sha256=${createHmac("sha256", secret).update(payload).digest("hex")}`;
  if (expected.length !== signature.length) return false;
  return timingSafeEqual(Buffer.from(expected), Buffer.from(signature));
}
