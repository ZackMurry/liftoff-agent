"use client";

import { useEffect, useMemo, useState } from "react";

type Filter = "all" | "failed" | "running" | "queued";
type Scenario = "crosswind" | "gps" | "dense";

const logPool = [
  "dropout window 2.0s @ wp12",
  "reacquire fix · 9 sats",
  "heading hold ok · err 0.51m",
  "gps jitter 1.3m · compensating",
  "velocity 13.8 m/s · stable",
  "cross-track err 0.58m · nominal",
  "waypoint reached · advancing"
];

const initialLogs = [
  "[t+0.2s] spawn px4 sitl + gazebo · world=gps_degraded",
  "[t+1.1s] mission upload ok · 40 waypoints",
  "[t+2.4s] inject gps noise sigma=1.5m",
  "[t+3.8s] cross-track err 0.42m · nominal"
];

function fmt(seconds: number) {
  return Math.floor(seconds / 60) + ":" + String(Math.floor(seconds) % 60).padStart(2, "0");
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export default function Home() {
  const [expanded, setExpanded] = useState<Record<Scenario, boolean>>({
    crosswind: true,
    gps: true,
    dense: false
  });
  const [filter, setFilter] = useState<Filter>("all");
  const [gpsProgress, setGpsProgress] = useState(23);
  const [gpsElapsed, setGpsElapsed] = useState(16);
  const [runElapsed, setRunElapsed] = useState(78);
  const [logs, setLogs] = useState(initialLogs);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setGpsProgress((current) => Math.min(93, current + 1.4 + Math.random() * 3));
      setGpsElapsed((current) => current + 1);
      setRunElapsed((current) => current + 1);
      setTick((current) => current + 1);
    }, 950);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (tick === 0 || tick % 2 !== 0) return;
    setLogs((current) => {
      const line = "[t+" + (gpsElapsed + 2).toFixed(1) + "s] " + logPool[((tick / 2) | 0) % logPool.length];
      return [...current, line].slice(-6);
    });
  }, [gpsElapsed, tick]);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      const toggle = target.closest<HTMLElement>("[data-toggle]")?.dataset.toggle as Scenario | undefined;
      if (toggle) {
        setExpanded((current) => ({ ...current, [toggle]: !current[toggle] }));
        return;
      }
      const nextFilter = target.closest<HTMLElement>("[data-filter]")?.dataset.filter as Filter | undefined;
      if (nextFilter) setFilter(nextFilter);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  const html = useMemo(() => {
    const gpsPct = Math.round(gpsProgress) + "%";
    const tabs = [
      { key: "all", label: "All", count: 3 },
      { key: "failed", label: "Failed", count: 1 },
      { key: "running", label: "Running", count: 1 },
      { key: "queued", label: "Queued", count: 1 }
    ] as const;
    const tabsMarkup = tabs.map((tab) => {
      const active = filter === tab.key;
      return `<div data-filter="${tab.key}" style="cursor:pointer;font-size:11.5px;font-family:'JetBrains Mono',monospace;padding:5px 11px;border-radius:7px;border:1px solid ${active ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0)"};background: ${active ? "rgba(255,255,255,0.08)" : "transparent"};color: ${active ? "#e6e8eb" : "#8a9099"};display:flex;align-items:center;gap:6px;transition:all .15s">${tab.label} <span style="opacity:.55">${tab.count}</span></div>`;
    }).join("");
    const gpsLogsMarkup = logs
      .map((line) => `<div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(line)}</div>`)
      .join("");

    return `<div style="font-family:'IBM Plex Sans',system-ui,sans-serif;color:#e6e8eb;--accent: #00e5a0;min-height:100vh;background:radial-gradient(1100px 520px at 82% -12%, rgba(0,229,160,0.07), transparent 60%), #0a0b0d;-webkit-font-smoothing:antialiased">

  <header style="display:flex;align-items:center;justify-content:space-between;padding:14px 28px;border-bottom:1px solid rgba(255,255,255,0.07);position:sticky;top:0;background:rgba(10,11,13,0.82);backdrop-filter:blur(14px);z-index:30">
    <div style="display:flex;align-items:center;gap:12px">
      <div style="width:32px;height:32px;display:flex;align-items:center;justify-content:center;border:1px solid rgba(255,255,255,0.12);border-radius:8px;background:#111317">
        <svg width="15" height="15" viewBox="0 0 14 14"><polygon points="7,1 12.5,13 7,10 1.5,13" fill="var(--accent,#00e5a0)"></polygon></svg>
      </div>
      <div style="display:flex;flex-direction:column;line-height:1.15">
        <span style="font-weight:600;font-size:15px;letter-spacing:0.01em">Liftoff</span>
        <span style="font-size:10.5px;color:#7d838c;font-family:'JetBrains Mono',monospace;letter-spacing:0.02em">AI validation engineer</span>
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:18px;font-family:'JetBrains Mono',monospace;font-size:12px">
      <div style="display:flex;align-items:center;gap:8px;padding:6px 12px;border:1px solid rgba(0,229,160,0.28);background:rgba(0,229,160,0.08);border-radius:20px;color:var(--accent,#00e5a0);letter-spacing:0.03em">
        <span style="width:7px;height:7px;border-radius:50%;background:var(--accent,#00e5a0);display:inline-block;animation:pulse 1.3s ease-in-out infinite"></span>
        RUN&nbsp;#4127 · LIVE
      </div>
      <span style="color:#7d838c">elapsed&nbsp;<span style="color:#e6e8eb">{{ runElapsedStr }}</span></span>
      <div style="width:30px;height:30px;border:1px solid rgba(255,255,255,0.1);border-radius:50%;background:#16181d;display:flex;align-items:center;justify-content:center;color:#7d838c;font-family:'IBM Plex Sans';font-weight:600;font-size:12px">NA</div>
    </div>
  </header>

  <main style="max-width:1320px;margin:0 auto;padding:24px 28px 56px">

    <!-- PR SUMMARY -->
    <section style="border:1px solid rgba(255,255,255,0.07);border-radius:12px;background:#111317;padding:20px 22px;margin-bottom:18px">
      <div style="display:grid;grid-template-columns:1fr 392px;gap:26px;align-items:start">
        <div>
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:9px">
            <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#7d838c;letter-spacing:0.04em">PULL REQUEST #1432</span>
            <span style="font-size:10.5px;font-family:'JetBrains Mono',monospace;color:var(--accent,#00e5a0);border:1px solid rgba(0,229,160,0.28);padding:1px 7px;border-radius:5px">open</span>
          </div>
          <h1 style="margin:0 0 12px;font-size:23px;font-weight:600;letter-spacing:-0.01em;line-height:1.2">Increase maximum waypoint velocity</h1>
          <p style="margin:0 0 16px;color:#9aa0a8;font-size:13.5px;line-height:1.55;max-width:54ch">Raises the mission waypoint speed cap from 8 to 14&nbsp;m/s to improve inspection throughput on long autonomous routes.</p>
          <div style="display:flex;flex-wrap:wrap;gap:8px;font-family:'JetBrains Mono',monospace;font-size:11.5px">
            <span style="display:flex;align-items:center;gap:6px;padding:4px 10px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:7px;color:#b6bcc4"><span style="width:6px;height:6px;border-radius:50%;background:#5a6069"></span>px4-autonomy</span>
            <span style="padding:4px 10px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:7px;color:#b6bcc4">feat/faster-waypoints&nbsp;→&nbsp;main</span>
            <span style="padding:4px 10px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:7px;color:#b6bcc4">@nadia</span>
            <span style="padding:4px 10px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:7px"><span style="color:var(--accent,#00e5a0)">+1</span> <span style="color:#ff5d5d">−1</span> · 1 file</span>
          </div>
        </div>
        <div style="border:1px solid rgba(255,255,255,0.08);border-radius:9px;overflow:hidden;background:#0c0d10">
          <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid rgba(255,255,255,0.06);font-family:'JetBrains Mono',monospace;font-size:11px;color:#8a9099">
            <span>src/navigator/mission_block.cpp</span><span><span style="color:var(--accent,#00e5a0)">+1</span> <span style="color:#ff5d5d">−1</span></span>
          </div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:12px;line-height:1.85;padding:8px 0">
            <div style="padding:0 12px;color:#6b7178">  <span style="color:#9aa0a8">#define</span> MAX_WP_ACCEL 6.0f</div>
            <div style="padding:0 12px;background:rgba(255,93,93,0.1);color:#ff8e8e;border-left:2px solid #ff5d5d">− <span style="color:#ffb3b3">#define</span> MAX_WP_VELOCITY 8.0f&nbsp;&nbsp;<span style="color:#a96a6a">// m/s</span></div>
            <div style="padding:0 12px;background:rgba(0,229,160,0.1);color:#7fe9c8;border-left:2px solid var(--accent,#00e5a0)">+ <span style="color:#a7f0d8">#define</span> MAX_WP_VELOCITY 14.0f <span style="color:#5fa98e">// m/s</span></div>
            <div style="padding:0 12px;color:#6b7178">  param_get(handle, &amp;_max_velocity);</div>
          </div>
        </div>
      </div>

      <div style="display:flex;align-items:center;gap:16px;margin-top:18px;padding-top:15px;border-top:1px solid rgba(255,255,255,0.06)">
        <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#7d838c;letter-spacing:0.04em">VALIDATION RUN</span>
        <span style="font-size:12.5px;color:#cdd2d8"><span style="color:#e6e8eb;font-weight:600">1</span> of 3 experiments complete</span>
        <div style="flex:1;display:flex;gap:6px;max-width:560px">
          <div style="flex:1;height:6px;border-radius:4px;background:var(--accent,#00e5a0)"></div>
          <div style="flex:1;height:6px;border-radius:4px;background:rgba(255,255,255,0.07);position:relative;overflow:hidden">
            <div style="position:absolute;inset:0;width: {{ gpsPct }};background:#f5b13d;border-radius:4px"></div>
            <div style="position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.55),transparent);width:40%;animation:sweep 1.5s linear infinite"></div>
          </div>
          <div style="flex:1;height:6px;border-radius:4px;background:rgba(255,255,255,0.07)"></div>
        </div>
        <span style="font-family:'JetBrains Mono',monospace;font-size:11.5px;color:#8a9099">{{ runElapsedStr }}</span>
      </div>
    </section>

    <!-- MAIN GRID -->
    <div style="display:grid;grid-template-columns:1fr 352px;gap:18px;align-items:start">

      <!-- EXPERIMENTS -->
      <section>
        <div style="display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:12px">
          <div>
            <h2 style="margin:0;font-size:15px;font-weight:600;letter-spacing:0.01em">Validation Experiments</h2>
            <p style="margin:4px 0 0;font-size:12px;color:#7d838c;font-family:'JetBrains Mono',monospace">agent-selected · 3 of 5 templates</p>
          </div>
          <div style="display:flex;gap:4px">
            {{ tabsMarkup }}
          </div>
        </div>

        <div style="display:flex;flex-direction:column;gap:12px">

          <!-- CARD: CROSSWIND (FAILED) -->
          <div style="display: {{ crosswindVis }};border:1px solid rgba(255,93,93,0.22);border-radius:11px;background:#111317;overflow:hidden">
            <div data-toggle="crosswind" style="cursor:pointer;display:flex;align-items:center;gap:14px;padding:15px 18px">
              <span style="width:9px;height:9px;border-radius:50%;background:#ff5d5d;box-shadow:0 0 0 4px rgba(255,93,93,0.13);flex-shrink:0"></span>
              <div style="min-width:0">
                <div style="font-size:14.5px;font-weight:600">Crosswind + tight turns</div>
                <div style="font-size:11px;color:#7d838c;font-family:'JetBrains Mono',monospace;margin-top:2px">template · crosswind</div>
              </div>
              <div style="flex:1"></div>
              <span style="font-family:'JetBrains Mono',monospace;font-size:11.5px;padding:5px 11px;border-radius:7px;background:rgba(255,93,93,0.12);color:#ff7a7a;border:1px solid rgba(255,93,93,0.22);white-space:nowrap">FAILED · cross-track 4.8 m</span>
              <svg width="13" height="13" viewBox="0 0 12 12" style="transform: {{ crosswindChev }};transition:transform .2s;flex-shrink:0"><path d="M2 4l4 4 4-4" stroke="#8a9099" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"></path></svg>
            </div>
            <div style="display: {{ crosswindOpen }};border-top:1px solid rgba(255,255,255,0.06);padding:16px 18px">
              <p style="margin:0 0 14px;font-size:13px;line-height:1.6;color:#aab0b8"><span style="color:var(--accent,#00e5a0);font-family:'JetBrains Mono',monospace;font-size:11px">WHY&nbsp;</span> Higher waypoint velocity increases lateral overshoot; crosswind amplifies cross-track error through tight turns.</p>
              <div style="display:flex;flex-wrap:wrap;gap:7px;margin-bottom:18px;font-family:'JetBrains Mono',monospace;font-size:11px">
                <span style="padding:3px 9px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#9aa0a8">crosswind 12 m/s</span>
                <span style="padding:3px 9px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#9aa0a8">gust 4 m/s</span>
                <span style="padding:3px 9px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#9aa0a8">6 tight turns</span>
                <span style="padding:3px 9px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#9aa0a8">0.8 km</span>
              </div>
              <div style="background:#0c0d10;border:1px solid rgba(255,255,255,0.07);border-radius:9px;padding:15px 16px;margin-bottom:14px">
                <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:11px">
                  <span style="font-size:12.5px;color:#9aa0a8">Max cross-track error</span>
                  <span style="font-family:'JetBrains Mono',monospace"><span style="font-size:19px;font-weight:600;color:#ff7a7a">4.8 m</span> <span style="font-size:11px;color:#7d838c">/ 2.5 m max</span></span>
                </div>
                <div style="position:relative;height:8px;border-radius:5px;background:rgba(255,255,255,0.06);overflow:hidden">
                  <div style="position:absolute;left:0;top:0;bottom:0;width:96%;background:linear-gradient(90deg,#ff5d5d,#ff7a7a);border-radius:5px"></div>
                </div>
                <div style="position:relative;height:14px">
                  <div style="position:absolute;left:50%;top:-2px;bottom:0;border-left:1px dashed rgba(255,255,255,0.35)"></div>
                  <span style="position:absolute;left:50%;top:2px;transform:translateX(4px);font-family:'JetBrains Mono',monospace;font-size:9.5px;color:#7d838c">threshold</span>
                </div>
              </div>
              <div style="display:flex;gap:22px;font-family:'JetBrains Mono',monospace;font-size:11.5px;color:#8a9099">
                <span>overshoot <span style="color:#cdd2d8">3.1 m</span></span>
                <span>completion <span style="color:#cdd2d8">100%</span></span>
                <span>duration <span style="color:#cdd2d8">1:48</span></span>
              </div>
            </div>
          </div>

          <!-- CARD: GPS (RUNNING) -->
          <div style="display: {{ gpsVis }};border:1px solid rgba(245,177,61,0.25);border-radius:11px;background:#111317;overflow:hidden">
            <div data-toggle="gps" style="cursor:pointer;display:flex;align-items:center;gap:14px;padding:15px 18px">
              <span style="width:9px;height:9px;border-radius:50%;background:#f5b13d;box-shadow:0 0 0 4px rgba(245,177,61,0.13);flex-shrink:0;animation:pulse 1.3s ease-in-out infinite"></span>
              <div style="min-width:0">
                <div style="font-size:14.5px;font-weight:600">GPS degradation</div>
                <div style="font-size:11px;color:#7d838c;font-family:'JetBrains Mono',monospace;margin-top:2px">template · gps_degraded</div>
              </div>
              <div style="flex:1"></div>
              <span style="font-family:'JetBrains Mono',monospace;font-size:11.5px;padding:5px 11px;border-radius:7px;background:rgba(245,177,61,0.12);color:#f5b13d;border:1px solid rgba(245,177,61,0.22);white-space:nowrap">RUNNING · {{ gpsPct }}</span>
              <svg width="13" height="13" viewBox="0 0 12 12" style="transform: {{ gpsChev }};transition:transform .2s;flex-shrink:0"><path d="M2 4l4 4 4-4" stroke="#8a9099" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"></path></svg>
            </div>
            <div style="display: {{ gpsOpen }};border-top:1px solid rgba(255,255,255,0.06);padding:16px 18px">
              <p style="margin:0 0 14px;font-size:13px;line-height:1.6;color:#aab0b8"><span style="color:var(--accent,#00e5a0);font-family:'JetBrains Mono',monospace;font-size:11px">WHY&nbsp;</span> Faster transit shortens the window to recover from position error; validates navigation robustness under degraded GPS.</p>
              <div style="display:flex;flex-wrap:wrap;gap:7px;margin-bottom:16px;font-family:'JetBrains Mono',monospace;font-size:11px">
                <span style="padding:3px 9px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#9aa0a8">noise σ=1.5 m</span>
                <span style="padding:3px 9px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#9aa0a8">dropout 2.0 s</span>
                <span style="padding:3px 9px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#9aa0a8">40 waypoints</span>
              </div>
              <div style="display:flex;align-items:center;justify-content:space-between;font-family:'JetBrains Mono',monospace;font-size:11.5px;margin-bottom:8px">
                <span style="color:#f5b13d">simulating…</span>
                <span style="color:#8a9099"><span style="color:#cdd2d8">{{ gpsPct }}</span> · {{ gpsElapsedStr }}</span>
              </div>
              <div style="position:relative;height:8px;border-radius:5px;background:rgba(255,255,255,0.06);overflow:hidden;margin-bottom:14px">
                <div style="position:absolute;left:0;top:0;bottom:0;width: {{ gpsPct }};background:linear-gradient(90deg,#d99428,#f5b13d);border-radius:5px;transition:width .6s ease"></div>
                <div style="position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.45),transparent);width:35%;animation:sweep 1.5s linear infinite"></div>
              </div>
              <div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--accent,#00e5a0);margin-bottom:14px">↳ cross-track error 0.6 m · within 2.5 m so far</div>
              <div style="background:#08090b;border:1px solid rgba(255,255,255,0.07);border-radius:8px;padding:11px 13px;font-family:'JetBrains Mono',monospace;font-size:11px;line-height:1.85;color:#8a9099;max-height:128px;overflow:hidden">
                {{ gpsLogsMarkup }}
                <div style="color:#f5b13d">▮<span style="animation:blink 1s step-end infinite">_</span></div>
              </div>
            </div>
          </div>

          <!-- CARD: DENSE (QUEUED) -->
          <div style="display: {{ denseVis }};border:1px solid rgba(255,255,255,0.07);border-radius:11px;background:#0f1014;overflow:hidden">
            <div data-toggle="dense" style="cursor:pointer;display:flex;align-items:center;gap:14px;padding:15px 18px">
              <span style="width:9px;height:9px;border-radius:50%;background:#4a5059;flex-shrink:0"></span>
              <div style="min-width:0">
                <div style="font-size:14.5px;font-weight:600;color:#cdd2d8">Dense waypoint mission</div>
                <div style="font-size:11px;color:#6b7178;font-family:'JetBrains Mono',monospace;margin-top:2px">template · dense_waypoints</div>
              </div>
              <div style="flex:1"></div>
              <span style="font-family:'JetBrains Mono',monospace;font-size:11.5px;padding:5px 11px;border-radius:7px;background:rgba(255,255,255,0.05);color:#8a9099;border:1px solid rgba(255,255,255,0.08);white-space:nowrap">QUEUED</span>
              <svg width="13" height="13" viewBox="0 0 12 12" style="transform: {{ denseChev }};transition:transform .2s;flex-shrink:0"><path d="M2 4l4 4 4-4" stroke="#6b7178" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"></path></svg>
            </div>
            <div style="display: {{ denseOpen }};border-top:1px solid rgba(255,255,255,0.06);padding:16px 18px">
              <p style="margin:0 0 14px;font-size:13px;line-height:1.6;color:#9aa0a8"><span style="color:var(--accent,#00e5a0);font-family:'JetBrains Mono',monospace;font-size:11px">WHY&nbsp;</span> Short segment lengths leave little room to correct at higher speed; stresses the controller through closely spaced waypoints.</p>
              <div style="display:flex;flex-wrap:wrap;gap:7px;margin-bottom:16px;font-family:'JetBrains Mono',monospace;font-size:11px">
                <span style="padding:3px 9px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#9aa0a8">40 waypoints</span>
                <span style="padding:3px 9px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#9aa0a8">3 m spacing</span>
                <span style="padding:3px 9px;background:#16181d;border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#9aa0a8">building inspection</span>
              </div>
              <div style="display:flex;align-items:center;gap:10px;font-family:'JetBrains Mono',monospace;font-size:11.5px;color:#7d838c">
                <span style="width:12px;height:12px;border:1.6px solid #4a5059;border-top-color:#8a9099;border-radius:50%;animation:spin 0.9s linear infinite;display:inline-block"></span>
                queued · starts after GPS degradation · est. 2:10
              </div>
            </div>
          </div>

        </div>
      </section>

      <!-- RIGHT RAIL -->
      <aside style="display:flex;flex-direction:column;gap:16px">

        <div style="border:1px solid rgba(255,255,255,0.07);border-radius:12px;background:#111317;padding:18px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
            <h2 style="margin:0;font-size:14px;font-weight:600">Agent Plan</h2>
            <span style="font-family:'JetBrains Mono',monospace;font-size:10px;color:#7d838c;border:1px solid rgba(255,255,255,0.09);padding:2px 7px;border-radius:5px">plan v1</span>
          </div>
          <div style="display:flex;flex-direction:column">
            <div style="display:flex;gap:12px;padding-bottom:14px;position:relative">
              <div style="position:absolute;left:4.5px;top:13px;bottom:-1px;width:1px;background:rgba(255,255,255,0.1)"></div>
              <span style="width:10px;height:10px;border-radius:50%;background:#5a6069;margin-top:3px;flex-shrink:0;z-index:1"></span>
              <p style="margin:0;font-size:12.5px;line-height:1.5;color:#aab0b8">Parsed diff — 1 file changed in <span style="color:#cdd2d8;font-family:'JetBrains Mono',monospace;font-size:11px">navigator/</span></p>
            </div>
            <div style="display:flex;gap:12px;padding-bottom:14px;position:relative">
              <div style="position:absolute;left:4.5px;top:13px;bottom:-1px;width:1px;background:rgba(255,255,255,0.1)"></div>
              <span style="width:10px;height:10px;border-radius:50%;background:var(--accent,#00e5a0);margin-top:3px;flex-shrink:0;z-index:1"></span>
              <p style="margin:0;font-size:12.5px;line-height:1.5;color:#cdd2d8"><span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--accent,#00e5a0)">MAX_WP_VELOCITY</span> raised 8 → 14 m/s (+75%)</p>
            </div>
            <div style="display:flex;gap:12px;padding-bottom:14px;position:relative">
              <div style="position:absolute;left:4.5px;top:13px;bottom:-1px;width:1px;background:rgba(255,255,255,0.1)"></div>
              <span style="width:10px;height:10px;border-radius:50%;background:#5a6069;margin-top:3px;flex-shrink:0;z-index:1"></span>
              <p style="margin:0;font-size:12.5px;line-height:1.5;color:#aab0b8">Affects mission-following &amp; waypoint tracking behavior</p>
            </div>
            <div style="display:flex;gap:12px;padding-bottom:14px;position:relative">
              <div style="position:absolute;left:4.5px;top:13px;bottom:-1px;width:1px;background:rgba(255,255,255,0.1)"></div>
              <span style="width:10px;height:10px;border-radius:50%;background:#5a6069;margin-top:3px;flex-shrink:0;z-index:1"></span>
              <p style="margin:0;font-size:12.5px;line-height:1.5;color:#aab0b8">Hypothesis: higher speed increases overshoot &amp; cross-track error in turns</p>
            </div>
            <div style="display:flex;gap:12px;position:relative">
              <span style="width:10px;height:10px;border-radius:50%;background:#5a6069;margin-top:3px;flex-shrink:0;z-index:1"></span>
              <p style="margin:0;font-size:12.5px;line-height:1.5;color:#aab0b8">Selected scenarios that exercise tracking under disturbance</p>
            </div>
          </div>
          <div style="margin-top:16px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.06)">
            <div style="font-family:'JetBrains Mono',monospace;font-size:10.5px;color:#7d838c;margin-bottom:9px">SELECTED 3 / 5 TEMPLATES</div>
            <div style="display:flex;flex-wrap:wrap;gap:6px;font-family:'JetBrains Mono',monospace;font-size:10.5px">
              <span style="padding:3px 8px;background:rgba(0,229,160,0.09);border:1px solid rgba(0,229,160,0.2);border-radius:6px;color:var(--accent,#00e5a0)">crosswind</span>
              <span style="padding:3px 8px;background:rgba(0,229,160,0.09);border:1px solid rgba(0,229,160,0.2);border-radius:6px;color:var(--accent,#00e5a0)">gps degradation</span>
              <span style="padding:3px 8px;background:rgba(0,229,160,0.09);border:1px solid rgba(0,229,160,0.2);border-radius:6px;color:var(--accent,#00e5a0)">dense waypoints</span>
              <span style="padding:3px 8px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#6b7178;text-decoration:line-through">tight turns</span>
              <span style="padding:3px 8px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.07);border-radius:6px;color:#6b7178;text-decoration:line-through">low-battery rtl</span>
            </div>
          </div>
        </div>

        <div style="border:1px solid rgba(255,93,93,0.22);border-radius:12px;background:linear-gradient(180deg,rgba(255,93,93,0.05),transparent 60%),#111317;padding:18px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
            <h2 style="margin:0;font-size:14px;font-weight:600">Recommendation</h2>
            <span style="display:flex;align-items:center;gap:6px;font-family:'JetBrains Mono',monospace;font-size:10px;color:#f5b13d;border:1px solid rgba(245,177,61,0.25);padding:2px 8px;border-radius:5px"><span style="width:5px;height:5px;border-radius:50%;background:#f5b13d;animation:pulse 1.3s ease-in-out infinite"></span>FORMING</span>
          </div>
          <div style="display:flex;align-items:center;gap:11px;margin-bottom:12px">
            <div style="width:34px;height:34px;border-radius:9px;background:rgba(255,93,93,0.13);border:1px solid rgba(255,93,93,0.25);display:flex;align-items:center;justify-content:center;flex-shrink:0">
              <svg width="16" height="16" viewBox="0 0 16 16"><path d="M8 1.5l6.5 11.5h-13z" fill="none" stroke="#ff7a7a" stroke-width="1.4" stroke-linejoin="round"></path><path d="M8 6v3.2M8 11.2v.1" stroke="#ff7a7a" stroke-width="1.5" stroke-linecap="round"></path></svg>
            </div>
            <div>
              <div style="font-size:16px;font-weight:600;color:#ff8e8e">{{ verdict }}</div>
              <div style="font-size:11px;color:#7d838c;font-family:'JetBrains Mono',monospace">preliminary · 1 of 3 in</div>
            </div>
          </div>
          <p style="margin:0 0 16px;font-size:12.5px;line-height:1.6;color:#aab0b8">Crosswind mission exceeded acceptable tracking error <span style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#cdd2d8">(4.8 m &gt; 2.5 m)</span>. Awaiting GPS &amp; dense-waypoint results before the review is posted.</p>
          <div style="display:flex;gap:8px">
            <a href="#" style="flex:1;text-align:center;text-decoration:none;font-size:12.5px;font-weight:500;padding:9px 0;border-radius:8px;border:1px solid rgba(255,255,255,0.12);color:#cdd2d8;background:#16181d">View PR on GitHub</a>
            <button style="flex:1;font-family:inherit;font-size:12.5px;font-weight:600;padding:9px 0;border-radius:8px;border:1px solid rgba(255,255,255,0.06);color:#5a6069;background:rgba(255,255,255,0.03);cursor:not-allowed">Post review</button>
          </div>
        </div>

      </aside>
    </div>
  </main>
</div>`
      .replace(/{{ runElapsedStr }}/g, fmt(runElapsed))
      .replace(/{{ gpsElapsedStr }}/g, fmt(gpsElapsed))
      .replace(/{{ gpsPct }}/g, gpsPct)
      .replace(/{{ verdict }}/g, "Request changes")
      .replace(/{{ tabsMarkup }}/g, tabsMarkup)
      .replace(/{{ gpsLogsMarkup }}/g, gpsLogsMarkup)
      .replace(/{{ crosswindOpen }}/g, expanded.crosswind ? "block" : "none")
      .replace(/{{ gpsOpen }}/g, expanded.gps ? "block" : "none")
      .replace(/{{ denseOpen }}/g, expanded.dense ? "block" : "none")
      .replace(/{{ crosswindChev }}/g, expanded.crosswind ? "rotate(180deg)" : "rotate(0deg)")
      .replace(/{{ gpsChev }}/g, expanded.gps ? "rotate(180deg)" : "rotate(0deg)")
      .replace(/{{ denseChev }}/g, expanded.dense ? "rotate(180deg)" : "rotate(0deg)")
      .replace(/{{ crosswindVis }}/g, filter === "all" || filter === "failed" ? "block" : "none")
      .replace(/{{ gpsVis }}/g, filter === "all" || filter === "running" ? "block" : "none")
      .replace(/{{ denseVis }}/g, filter === "all" || filter === "queued" ? "block" : "none");
  }, [expanded, filter, gpsElapsed, gpsProgress, logs, runElapsed]);

  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}
