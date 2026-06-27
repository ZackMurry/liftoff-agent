"use client";

import { useMemo, useState } from "react";

type Filter = "all" | "active" | "failed" | "passed" | "merged";
type PullStatus = "active" | "failed" | "passed" | "merged";
type Recommendation = "forming" | "request" | "approve" | "nosim" | "merged";
type DotState = "pass" | "fail" | "run" | "queue";

export type PullRequestRow = {
  id: string;
  number: string;
  title: string;
  branch: string;
  author: string;
  status: PullStatus;
  rec: Recommendation;
  dots: DotState[];
  latestExperimentStatus?: string;
  headSha?: string;
  time?: string;
};

export type DashboardCounts = {
  all: number;
  active: number;
  failed: number;
  passed: number;
  merged: number;
};

const accent = "#00e5a0";

function fmt(seconds: number) {
  return Math.floor(seconds / 60) + ":" + String(Math.floor(seconds) % 60).padStart(2, "0");
}

export default function Dashboard({
  prs,
  counts,
}: {
  prs: PullRequestRow[];
  counts: DashboardCounts;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const shownPrs = useMemo(() => {
    const q = query.trim().toLowerCase();
    return prs.filter((pr) => {
      const filterMatch = filter === "all" || filter === pr.status;
      const search = `#${pr.number} ${pr.title} ${pr.author} ${pr.branch} ${pr.headSha ?? ""}`.toLowerCase();
      return filterMatch && (!q || search.includes(q));
    });
  }, [filter, query, prs]);

  const tabs = [
    { key: "all" as const, label: "All", count: counts.all },
    { key: "active" as const, label: "Active", count: counts.active },
    { key: "failed" as const, label: "Failed", count: counts.failed },
    { key: "passed" as const, label: "Passed", count: counts.passed },
    { key: "merged" as const, label: "Merged", count: counts.merged },
  ];

  return (
    <div className="home-shell">
      <header className="home-header">
        <div className="header-left">
          <div className="brand">
            <div className="brand-mark">
              <svg width="15" height="15" viewBox="0 0 14 14" aria-hidden="true">
                <polygon points="7,1 12.5,13 7,10 1.5,13" fill="var(--accent)" />
              </svg>
            </div>
            <span className="brand-name">Liftoff</span>
          </div>
          <nav className="home-nav" aria-label="Primary">
            <span className="nav-active">Pull requests</span>
            <span>Templates</span>
            <span>Activity</span>
          </nav>
        </div>
        <div className="header-right">
          <span className="active-pill"><span />{counts.active} ACTIVE</span>
          <span className="avatar">NA</span>
        </div>
      </header>

      <main className="home-main">
        <section className="intro">
          <h1>Pull requests</h1>
          <p>
            Liftoff reviews every change to autonomy code - selecting which flight
            simulations should run for each diff, then recommending whether to merge.
          </p>
        </section>

        <section className="stats-grid" aria-label="Repository validation summary">
          <Stat label="OPEN PRS" value={String(counts.all)} caption="under validation" />
          <Stat
            label="PASS RATE"
            value={counts.all > 0 ? `${Math.round((counts.passed / counts.all) * 100)}%` : "-"}
            caption="of validated PRs"
            valueClass="good"
          />
          <Stat
            label="ACTIVE RUNS"
            value={String(counts.active)}
            caption="simulating now"
            valueClass={counts.active > 0 ? "with-dot" : undefined}
          />
          <Stat label="TOTAL" value={String(counts.all)} caption="pull requests" />
        </section>

        <section className="pr-panel">
          <div className="panel-toolbar">
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
            <label className="search">
              <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
                <circle cx="6" cy="6" r="4.3" fill="none" stroke="#6b7178" strokeWidth="1.4" />
                <path d="M9.2 9.2L12 12" stroke="#6b7178" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search PRs, authors, branches"
              />
            </label>
          </div>

          <div className="legend">
            <Legend color={accent} label="pass" />
            <Legend color="#ff5d5d" label="fail" />
            <Legend color="#f5b13d" label="running" />
            <Legend color="#3a3f47" label="queued" />
            <span className="legend-spacer" />
            <span>experiments - recommendation</span>
          </div>

          <div className="pr-list">
            {shownPrs.map((pr) => (
              <PullRequestItem key={pr.id} pr={pr} />
            ))}
          </div>

          {shownPrs.length === 0 ? (
            <div className="empty">No pull requests match this view.</div>
          ) : null}
        </section>
      </main>
    </div>
  );
}

function Stat({
  label,
  value,
  caption,
  valueClass,
}: {
  label: string;
  value: string;
  caption: string;
  valueClass?: string;
}) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className={valueClass ? `stat-value ${valueClass}` : "stat-value"}>
        {valueClass === "with-dot" ? <span className="pulse-dot" /> : null}
        {value}
      </div>
      <div className="stat-caption">{caption}</div>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="legend-item">
      <span style={{ background: color }} />
      {label}
    </span>
  );
}

function PullRequestItem({ pr }: { pr: PullRequestRow }) {
  const status = statusMap[pr.status];
  const rec = recMap[pr.rec];
  const isActive = pr.status === "active";

  return (
    <a className="pr-row" href={`/pulls/${pr.id}`} aria-label={`${pr.title} pull request ${pr.number}`}>
      <span
        className="status-dot"
        style={{
          background: status.color,
          boxShadow: status.ring,
          animation: status.animation,
        }}
      />
      <div className="pr-title-block">
        <div className="title-line">
          <span>{pr.title}</span>
          <em>#{pr.number}</em>
        </div>
        <div className="meta-line">
          {pr.branch} - @{pr.author} {pr.headSha ? `- ${pr.headSha}` : ""} - {pr.time ?? ""}
        </div>
      </div>

      <div className="experiment-dots">
        {pr.dots.length ? (
          pr.dots.map((dot, index) => (
            <span
              key={`${dot}-${index}`}
              style={{
                background: dotColor[dot],
                animation: dot === "run" ? "pulse 1.3s ease-in-out infinite" : "none",
              }}
            />
          ))
        ) : (
          <span className="skipped">- skipped</span>
        )}
      </div>

      <span className="rec-badge" style={{ color: rec.color, background: rec.bg, borderColor: rec.bd }}>
        {rec.dot ? <span style={{ background: rec.color }} /> : null}
        {rec.text}
      </span>

      <div className="right-metric">
        <div>{pr.latestExperimentStatus ?? pr.time}</div>
        {isActive ? (
          <div className="mini-progress">
            <span style={{ width: "50%" }} />
            <em />
          </div>
        ) : null}
      </div>

      <svg width="14" height="14" viewBox="0 0 14 14" className="row-chevron" aria-hidden="true">
        <path d="M5 3l4 4-4 4" stroke="#8a9099" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </a>
  );
}

const dotColor: Record<DotState, string> = {
  pass: accent,
  fail: "#ff5d5d",
  run: "#f5b13d",
  queue: "#3a3f47",
};

const statusMap: Record<PullStatus, { color: string; ring: string; animation: string }> = {
  active: {
    color: "#f5b13d",
    ring: "0 0 0 4px rgba(245,177,61,0.12)",
    animation: "pulse 1.3s ease-in-out infinite",
  },
  failed: {
    color: "#ff5d5d",
    ring: "0 0 0 4px rgba(255,93,93,0.10)",
    animation: "none",
  },
  passed: {
    color: accent,
    ring: "0 0 0 4px rgba(0,229,160,0.10)",
    animation: "none",
  },
  merged: {
    color: "#b388ff",
    ring: "0 0 0 4px rgba(179,136,255,0.10)",
    animation: "none",
  },
};

const recMap: Record<Recommendation, { text: string; color: string; bg: string; bd: string; dot: boolean }> = {
  forming: {
    text: "Forming",
    color: "#f5b13d",
    bg: "rgba(245,177,61,0.12)",
    bd: "rgba(245,177,61,0.24)",
    dot: true,
  },
  request: {
    text: "Request changes",
    color: "#ff8e8e",
    bg: "rgba(255,93,93,0.12)",
    bd: "rgba(255,93,93,0.24)",
    dot: false,
  },
  approve: {
    text: "Approve",
    color: accent,
    bg: "rgba(0,229,160,0.10)",
    bd: "rgba(0,229,160,0.24)",
    dot: false,
  },
  nosim: {
    text: "No sim required",
    color: "#9aa0a8",
    bg: "rgba(255,255,255,0.05)",
    bd: "rgba(255,255,255,0.1)",
    dot: false,
  },
  merged: {
    text: "Merged",
    color: "#b388ff",
    bg: "rgba(179,136,255,0.12)",
    bd: "rgba(179,136,255,0.24)",
    dot: false,
  },
};
