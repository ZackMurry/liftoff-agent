import { tool } from "ai";
import { z } from "zod";
import { postReviewComment, getInstallationOctokit } from "./github";
import {
  insertExperiment,
  completeExperiment,
  updateExperimentLogs,
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

function resultLogs(result: Record<string, unknown>) {
  const candidates = [result.logs, result.log_lines, result.output];
  for (const candidate of candidates) {
    if (Array.isArray(candidate)) {
      return candidate
        .filter((line): line is string => typeof line === "string")
        .slice(-100);
    }
    if (typeof candidate === "string") {
      return candidate.split("\n").filter(Boolean).slice(-100);
    }
  }
  return [];
}

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
  let experimentHasRun = false;

  return {
    run_experiment: tool({
      description:
        "Run the single allowed drone simulation experiment for this PR on the sim server. The scenario must be exactly one of: waypoint_mission, crosswind, tight_turns, low_battery_rtl. Returns the full ExperimentResult JSON with metrics and outcomes.",
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
        if (experimentHasRun) {
          return {
            status: "error",
            error: "Only one experiment may be run per PR review.",
          };
        }
        experimentHasRun = true;

        const expId = await insertExperiment({
          pullRequestId: prDbId,
          scenario,
          params: { ...params, replications, speed_factor },
        });
        const startedAt = Date.now();
        const logs: string[] = [];
        const appendLog = async (message: string) => {
          const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1);
          logs.push(`[t+${elapsed}s] ${message}`);
          await updateExperimentLogs(expId, logs.slice(-100));
        };

        await appendLog(`created experiment row for ${scenario}`);

        const headers: Record<string, string> = { "Content-Type": "application/json" };
        if (process.env.SIM_SERVER_AUTH_TOKEN) {
          headers.Authorization = `Bearer ${process.env.SIM_SERVER_AUTH_TOKEN}`;
        }
        await appendLog(`posting ${scenario} run to ${SIM_SERVER_URL}/run`);
        let heartbeat: ReturnType<typeof setInterval> | null = setInterval(() => {
          void appendLog("waiting for sim server response");
        }, 15_000);
        let res: Response;
        try {
          res = await fetch(`${SIM_SERVER_URL}/run`, {
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
        } catch (error) {
          if (heartbeat) {
            clearInterval(heartbeat);
            heartbeat = null;
          }
          await appendLog("sim server request failed");
          const errorResult = {
            status: "error",
            error: error instanceof Error ? error.message : "Unknown sim server request failure",
            logs,
          };
          await completeExperiment(expId, errorResult);
          return errorResult;
        }
        if (heartbeat) {
          clearInterval(heartbeat);
          heartbeat = null;
        }
        await appendLog(`sim server returned HTTP ${res.status}`);
        if (!res.ok) {
          const text = await res.text();
          await appendLog("captured sim server error response");
          const errorResult = {
            status: "error",
            error: `Sim server returned ${res.status}: ${text}`,
            logs,
          };
          await completeExperiment(expId, errorResult);
          return errorResult;
        }
        const result = await res.json();
        const simLogs = resultLogs(result);
        if (simLogs.length) {
          logs.push(...simLogs);
          await updateExperimentLogs(expId, logs.slice(-100));
        } else {
          await appendLog("parsed experiment result JSON");
        }
        const resultWithLogs = {
          ...result,
          logs: logs.slice(-100),
        };
        await appendLog(`completed experiment with status ${String(result.status ?? "unknown")}`);
        resultWithLogs.logs = logs.slice(-100);
        await completeExperiment(expId, resultWithLogs);
        return resultWithLogs;
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
