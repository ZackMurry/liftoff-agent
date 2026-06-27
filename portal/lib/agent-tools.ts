import { tool } from "ai";
import { z } from "zod";
import { postReviewComment, getInstallationOctokit } from "./github";

const SIM_SERVER_URL = process.env.SIM_SERVER_URL ?? "http://localhost:8000";

export type ExperimentSource = {
  clone_url: string;
  full_name: string;
  head_ref: string;
  head_sha: string;
  token?: string;
};

export function makeTools({
  octokit,
  owner,
  repo,
  pullNumber,
  source,
}: {
  octokit: Awaited<ReturnType<typeof getInstallationOctokit>>;
  owner: string;
  repo: string;
  pullNumber: number;
  source: ExperimentSource;
}) {
  return {
    run_experiment: tool({
      description:
        "Run a drone simulation experiment on the sim server. Returns the full ExperimentResult JSON with metrics and outcomes.",
      inputSchema: z.object({
        scenario: z.string().describe("Scenario name, e.g. 'windy_landing'"),
        params: z
          .record(z.string(), z.unknown())
          .optional()
          .describe("Override parameters for the scenario"),
        replications: z
          .number()
          .int()
          .min(1)
          .max(100)
          .default(10)
          .describe("Number of replications to run"),
        speed_factor: z
          .number()
          .min(1)
          .max(100)
          .default(10)
          .describe("Simulation speed multiplier"),
      }),
      execute: async ({ scenario, params, replications, speed_factor }) => {
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        if (process.env.SIM_SERVER_AUTH_TOKEN) {
          headers.Authorization = `Bearer ${process.env.SIM_SERVER_AUTH_TOKEN}`;
        }
        const res = await fetch(`${SIM_SERVER_URL}/run`, {
          method: "POST",
          headers,
          body: JSON.stringify({
            scenario,
            params,
            replications,
            speed_factor,
            source,
          }),
        });
        if (!res.ok) {
          const text = await res.text();
          return { error: `Sim server returned ${res.status}: ${text}` };
        }
        return await res.json();
      },
    }),

    post_review: tool({
      description:
        "Post a review comment on the GitHub PR with your analysis and experiment results.",
      inputSchema: z.object({
        body: z
          .string()
          .describe(
            "Markdown-formatted review comment with risk analysis and experiment results",
          ),
      }),
      execute: async ({ body }) => {
        await postReviewComment(octokit, owner, repo, pullNumber, body);
        return { success: true };
      },
    }),
  };
}
