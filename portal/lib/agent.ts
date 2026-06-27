import { generateText, isStepCount } from "ai";
import { openai } from "@ai-sdk/openai";
import { ExperimentSource, makeTools } from "./agent-tools";
import { getInstallationOctokit } from "./github";

const MAX_DIFF_CHARS = 100_000;

const SYSTEM_PROMPT = `You are Liftoff Agent, an AI reviewer for drone flight-software pull requests.

Your job:
1. Read the PR diff carefully.
2. Identify changes that could affect flight safety — e.g. control loops, parameter tuning, state-machine transitions, altitude/speed limits, failsafe logic, sensor processing.
3. For each risk you find, select 1–4 simulation scenarios to test. Use the run_experiment tool.
   You may ONLY use these scenario names, exactly as written:
   - waypoint_mission
   - crosswind
   - tight_turns
   - low_battery_rtl
   Do not invent scenario names such as crosswind_mission, crosswind_stability, emergency_stop, gps_degradation, or waypoint_accuracy.
4. Interpret the experiment results. Look for increased crash rates, degraded performance metrics, or safety-margin violations.
5. Post a single review comment using post_review with a clear markdown summary:
   - **Risk Assessment** — what the diff changes and why it matters
   - **Experiments Run** — which scenarios you tested and why
   - **Results** — key metrics, pass/fail, comparison to baselines
   - **Recommendation** — approve, request changes, or flag for human review

Be concise and evidence-driven. If the diff has no flight-safety impact, say so and approve.`;

export async function runAgent({
  diff,
  prTitle,
  prBody,
  octokit,
  owner,
  repo,
  pullNumber,
  source,
  prDbId,
}: {
  diff: string;
  prTitle: string;
  prBody: string;
  octokit: Awaited<ReturnType<typeof getInstallationOctokit>>;
  owner: string;
  repo: string;
  pullNumber: number;
  source: ExperimentSource;
  prDbId: string;
}) {
  const truncatedDiff =
    diff.length > MAX_DIFF_CHARS
      ? diff.slice(0, MAX_DIFF_CHARS) + "\n\n... [diff truncated] ..."
      : diff;

  const tools = makeTools({ octokit, owner, repo, pullNumber, source, prDbId });

  const result = await generateText({
    model: openai("gpt-4o"),
    stopWhen: isStepCount(10),
    tools,
    system: SYSTEM_PROMPT,
    prompt: `## PR: ${prTitle}\n\n${prBody ?? ""}\n\n## Diff\n\n\`\`\`diff\n${truncatedDiff}\n\`\`\``,
  });

  return result;
}
