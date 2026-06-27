import { tool } from "ai";
import { z } from "zod";
import { postReviewComment, getInstallationOctokit } from "./github";
import {
  insertExperiment,
  completeExperiment,
  updatePullRequestStatus,
} from "./db";

const SIM_SERVER_URL = process.env.SIM_SERVER_URL ?? "http://localhost:8000";
const VALID_SCENARIOS = [
  "waypoint_mission",
  "crosswind",
  "tight_turns",
  "low_battery_rtl",
] as const;

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
  prDbId,
}: {
  octokit: Awaited<ReturnType<typeof getInstallationOctokit>>;
  owner: string;
  repo: string;
  pullNumber: number;
  source: ExperimentSource;
  prDbId: string;
}) {
  return {
    run_experiment: tool({
      description:
        "Run a drone simulation experiment on the sim server. The scenario must be exactly one of: waypoint_mission, crosswind, tight_turns, low_battery_rtl. Returns the full ExperimentResult JSON with metrics and outcomes.",
      inputSchema: z.object({
        scenario: z
          .enum(VALID_SCENARIOS)
          .describe("Scenario name. Must be exactly one of the enum values."),
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
        const expId = await insertExperiment({
          pullRequestId: prDbId,
          scenario,
          params: { ...params, replications, speed_factor },
        });

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
          const errorResult = { status: "error", error: `Sim server returned ${res.status}: ${text}` };
          await completeExperiment(expId, errorResult);
          return errorResult;
        }
        const result = await res.json();
        await completeExperiment(expId, result);
        return result;
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

        const recommendation = body.toLowerCase().includes("request changes")
          ? "request"
          : body.toLowerCase().includes("approve")
            ? "approve"
            : "forming";
        const status = recommendation === "approve" ? "passed" : "failed";
        await updatePullRequestStatus(prDbId, status, recommendation, body);

        return { success: true };
      },
    }),
  };
}
