"use client";

import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

type Filter = "all" | "failed" | "running" | "queued";

export type RunPageData = {
  id: string;
  number: string;
  repository: string;
  title: string;
  branch: string;
  author: string;
  headSha: string;
  status: string;
  recommendation: string;
  reviewBody: string | null;
  diffLength: number | null;
  updatedAt: string;
  experiments: RunExperiment[];
};

type RunExperiment = {
  id: string;
  scenario: string;
  status: string;
  verdict: string | null;
  error: string | null;
  params: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  logs: string[];
  passCriteria: Record<string, unknown> | null;
  startedAt: string | null;
  finishedAt: string | null;
};

const accent = "#00e5a0";

function fmt(seconds: number) {
  return Math.floor(seconds / 60) + ":" + String(Math.floor(seconds) % 60).padStart(2, "0");
}

function relativeTime(dateStr: string | null) {
  if (!dateStr) return "not started";
  const diff = Math.max(0, Date.now() - new Date(dateStr).getTime());
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return days === 1 ? "yesterday" : `${days}d ago`;
}

function statusTone(status: string) {
  if (status === "passed") return { color: accent, border: "rgba(0,229,160,0.24)", bg: "rgba(0,229,160,0.10)" };
  if (status === "failed" || status === "error") return { color: "#ff7a7a", border: "rgba(255,93,93,0.24)", bg: "rgba(255,93,93,0.12)" };
  if (status === "running") return { color: "#f5b13d", border: "rgba(245,177,61,0.24)", bg: "rgba(245,177,61,0.12)" };
  return { color: "#8a9099", border: "rgba(255,255,255,0.10)", bg: "rgba(255,255,255,0.05)" };
}

function visibleStatus(status: string): Filter {
  if (status === "failed" || status === "error") return "failed";
  if (status === "running") return "running";
  return "queued";
}

export default function RunPage({ data }: { data: RunPageData }) {
  const router = useRouter();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [filter, setFilter] = useState<Filter>("all");
  const [now, setNow] = useState(() => Date.now());

  const experiments = data.experiments.length
    ? data.experiments
    : [
        {
          id: "pending",
          scenario: "pending",
          status: "queued",
          verdict: "No experiment has started yet.",
          error: null,
          params: null,
          result: null,
          logs: [],
          passCriteria: null,
          startedAt: null,
          finishedAt: null,
        },
      ];

  useEffect(() => {
    setExpanded(Object.fromEntries(experiments.slice(0, 2).map((experiment) => [experiment.id, true])));
  }, [data.id]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const filteredExperiments = useMemo(
    () => experiments.filter((experiment) => filter === "all" || visibleStatus(experiment.status) === filter),
    [experiments, filter],
  );

  const completed = data.experiments.filter((experiment) => ["passed", "failed", "error"].includes(experiment.status)).length;
  const failed = data.experiments.some((experiment) => ["failed", "error"].includes(experiment.status));
  const running = data.experiments.some((experiment) => experiment.status === "running");
  const shouldRefresh = running || data.status === "active" || data.recommendation === "forming";
  const runStartedAt = data.experiments
    .map((experiment) => experiment.startedAt ? new Date(experiment.startedAt).getTime() : null)
    .filter((time): time is number => typeof time === "number" && !Number.isNaN(time))
    .sort((a, b) => a - b)[0] ?? null;
  const elapsed = runStartedAt ? Math.max(0, (now - runStartedAt) / 1000) : 0;

  useEffect(() => {
    if (!shouldRefresh) return;
    const refresh = window.setInterval(() => router.refresh(), 3000);
    return () => window.clearInterval(refresh);
  }, [router, shouldRefresh]);

  const headlineStatus = failed ? "Request changes" : running ? "Forming" : data.recommendation === "approve" ? "Approve" : "Forming";
  const tabs = [
    { key: "all" as const, label: "All", count: experiments.length },
    { key: "failed" as const, label: "Failed", count: experiments.filter((e) => visibleStatus(e.status) === "failed").length },
    { key: "running" as const, label: "Running", count: experiments.filter((e) => visibleStatus(e.status) === "running").length },
    { key: "queued" as const, label: "Queued", count: experiments.filter((e) => visibleStatus(e.status) === "queued").length },
  ];

  return (
    <div className="run-shell">
      <header className="run-header">
        <a className="run-brand" href="/">
          <span className="brand-mark">
            <svg width="15" height="15" viewBox="0 0 14 14" aria-hidden="true">
              <polygon points="7,1 12.5,13 7,10 1.5,13" fill="var(--accent)" />
            </svg>
          </span>
          <span>
            <strong>Liftoff</strong>
            <em>AI validation engineer</em>
          </span>
        </a>
        <div className="run-header-meta">
          <span className={running ? "run-live-pill" : "run-live-pill muted"}>
            <span />
            RUN #{data.number} {running ? "- LIVE" : ""}
          </span>
          <span>elapsed <strong>{fmt(elapsed)}</strong></span>
          <span className="avatar">NA</span>
        </div>
      </header>

      <main className="run-main">
        <section className="run-summary-card">
          <div className="run-summary-grid">
            <div>
              <div className="run-eyebrow">
                <span>PULL REQUEST #{data.number}</span>
                <em>{data.status}</em>
              </div>
              <h1>{data.title}</h1>
              <p>
                Latest Liftoff validation run for commit <code>{data.headSha}</code>. This page is
                available whether the run is queued, running, failed, or complete.
              </p>
              <div className="run-tags">
                <span>{data.repository}</span>
                <span>{data.branch} - main</span>
                <span>@{data.author}</span>
                <span>{relativeTime(data.updatedAt)}</span>
              </div>
            </div>

            <div className="run-code-card">
              <div><span>head</span><strong>{data.headSha}</strong></div>
              <div><span>diff</span><strong>{data.diffLength ? `${data.diffLength.toLocaleString()} chars` : "pending"}</strong></div>
              <div><span>experiments</span><strong>{data.experiments.length || "not started"}</strong></div>
            </div>
          </div>

          <div className="run-progress-row">
            <span>VALIDATION RUN</span>
            <strong>{completed}</strong> of <strong>{experiments.length}</strong> experiments complete
            <div className="run-progress-track">
              {experiments.map((experiment) => {
                const tone = statusTone(experiment.status);
                return <span key={experiment.id} style={{ background: ["passed", "failed", "error"].includes(experiment.status) ? tone.color : "rgba(255,255,255,0.07)" }} />;
              })}
            </div>
            <em>{fmt(elapsed)}</em>
          </div>
        </section>

        <div className="run-content-grid">
          <section>
            <div className="run-section-head">
              <div>
                <h2>Validation Experiments</h2>
                <p>agent-selected - latest run</p>
              </div>
              <div className="tabs">
                {tabs.map((tab) => (
                  <button
                    key={tab.key}
                    className={filter === tab.key ? "tab active" : "tab"}
                    onClick={() => setFilter(tab.key)}
                    type="button"
                  >
                    {tab.label} <span>{tab.count}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="run-experiment-list">
              {filteredExperiments.map((experiment) => (
                <ExperimentCard
                  key={experiment.id}
                  experiment={experiment}
                  open={expanded[experiment.id] ?? false}
                  onToggle={() => setExpanded((current) => ({ ...current, [experiment.id]: !current[experiment.id] }))}
                />
              ))}
            </div>
          </section>

          <aside className="run-side">
            <div className="run-side-card">
              <div className="run-side-head">
                <h2>Agent Plan</h2>
                <span>plan v1</span>
              </div>
              <TimelineItem text="Parsed diff and stored PR head metadata." active={false} />
              <TimelineItem text="Mapped code changes to flight-risk scenarios." active />
              <TimelineItem text="Launched PX4/Gazebo on the experiment server." active={running} />
              <TimelineItem text="Collected user-owned experiment results." active={completed > 0} last />
              <div className="selected-templates">
                <div>SELECTED TEMPLATES</div>
                <span>crosswind</span>
                <span>tight turns</span>
                <span>low-battery rtl</span>
              </div>
            </div>

            <div className={failed ? "run-recommendation bad" : "run-recommendation"}>
              <div className="run-side-head">
                <h2>Recommendation</h2>
                <span>{headlineStatus.toUpperCase()}</span>
              </div>
              <strong>{headlineStatus}</strong>
              <MarkdownRecommendation
                text={data.reviewBody || latestVerdict(experiments) || "Waiting for the first experiment result."}
              />
              <div className="run-actions">
                <a href={`https://github.com/${data.repository}/pull/${data.number}`}>View PR on GitHub</a>
                <button disabled>Post review</button>
              </div>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}

function ExperimentCard({
  experiment,
  open,
  onToggle,
}: {
  experiment: RunExperiment;
  open: boolean;
  onToggle: () => void;
}) {
  const tone = statusTone(experiment.status);
  const params = experiment.params ? Object.entries(experiment.params).slice(0, 4) : [];
  const passCriteria = experiment.passCriteria ? Object.entries(experiment.passCriteria) : [];
  const logs = experiment.logs.slice(-100);

  return (
    <div className="run-exp-card" style={{ borderColor: tone.border }}>
      <button className="run-exp-top" onClick={onToggle} type="button">
        <span className="status-dot" style={{ background: tone.color, boxShadow: `0 0 0 4px ${tone.bg}`, animation: experiment.status === "running" ? "pulse 1.3s ease-in-out infinite" : "none" }} />
        <span>
          <strong>{scenarioLabel(experiment.scenario)}</strong>
          <em>template - {experiment.scenario}</em>
        </span>
        <b style={{ color: tone.color, background: tone.bg, borderColor: tone.border }}>{experiment.status}</b>
        <svg width="13" height="13" viewBox="0 0 12 12" style={{ transform: open ? "rotate(180deg)" : "rotate(0deg)" }} aria-hidden="true">
          <path d="M2 4l4 4 4-4" stroke="#8a9099" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open ? (
        <div className="run-exp-body">
          <p><span>WHY</span> {experiment.verdict || experiment.error || "Queued for validation by the Liftoff agent."}</p>
          <div className="run-param-row">
            {params.length ? params.map(([key, value]) => <span key={key}>{key}: {String(value)}</span>) : <span>params pending</span>}
          </div>
          <div className="run-metric-box">
            {passCriteria.length ? passCriteria.map(([key, value]) => (
              <div key={key}>
                <span>{key}</span>
                <strong>{String(value)}</strong>
              </div>
            )) : (
              <div>
                <span>result</span>
                <strong>{experiment.status === "queued" ? "pending" : experiment.status}</strong>
              </div>
            )}
          </div>
          {logs.length ? (
            <div className="run-log-box">
              {logs.map((line, index) => <div key={`${line}-${index}`}>{line}</div>)}
              {experiment.status === "running" ? <div className="run-cursor">▮</div> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function TimelineItem({ text, active, last }: { text: string; active: boolean; last?: boolean }) {
  return (
    <div className={last ? "timeline-item last" : "timeline-item"}>
      <span className={active ? "active" : ""} />
      <p>{text}</p>
    </div>
  );
}

function MarkdownRecommendation({ text }: { text: string }) {
  const lines = normalizeMarkdown(text)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  return (
    <div className="markdown-recommendation">
      {lines.map((line, index) => {
        const heading = line.match(/^(#{1,6})\s+(.+)$/);
        if (heading) {
          const level = Math.min(heading[1].length, 4);
          return (
            <MarkdownHeading key={`${line}-${index}`} level={level}>
              {renderInlineMarkdown(heading[2])}
            </MarkdownHeading>
          );
        }

        const ordered = line.match(/^(\d+)\.\s+(.+)$/);
        if (ordered) {
          return (
            <div className="markdown-list-line" key={`${line}-${index}`}>
              <span>{ordered[1]}.</span>
              <p>{renderInlineMarkdown(ordered[2])}</p>
            </div>
          );
        }

        const bullet = line.match(/^[-*]\s+(.+)$/);
        if (bullet) {
          return (
            <div className="markdown-list-line" key={`${line}-${index}`}>
              <span>-</span>
              <p>{renderInlineMarkdown(bullet[1])}</p>
            </div>
          );
        }

        return <p key={`${line}-${index}`}>{renderInlineMarkdown(line)}</p>;
      })}
    </div>
  );
}

function MarkdownHeading({
  level,
  children,
}: {
  level: number;
  children: ReactNode;
}) {
  if (level === 1) return <h1>{children}</h1>;
  if (level === 2) return <h2>{children}</h2>;
  if (level === 3) return <h3>{children}</h3>;
  return <h4>{children}</h4>;
}

function normalizeMarkdown(text: string) {
  return text
    .replace(/\r\n/g, "\n")
    .replace(/\s+(#{1,6}\s+)/g, "\n\n$1")
    .replace(/\s+(\d+\.\s+)/g, "\n$1")
    .replace(/\s+([-*]\s+\*\*)/g, "\n$1");
}

function renderInlineMarkdown(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

function scenarioLabel(scenario: string) {
  return scenario
    .split(/[_-]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function latestVerdict(experiments: RunExperiment[]) {
  return [...experiments].reverse().find((experiment) => experiment.verdict || experiment.error)?.verdict ?? null;
}
