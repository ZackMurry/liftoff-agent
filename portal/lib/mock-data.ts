import {
  AlertTriangle,
  BatteryWarning,
  CircleGauge,
  GitPullRequest,
  Navigation,
  Radio,
  Route,
  ShieldCheck,
  Wind
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type ExperimentStatus = "passed" | "failed" | "running" | "queued";

export type ExperimentMetric = {
  label: string;
  value: string;
  limit: string;
  state: "ok" | "warn" | "bad";
};

export type Experiment = {
  id: string;
  name: string;
  template: string;
  icon: LucideIcon;
  status: ExperimentStatus;
  progress: number;
  elapsed: string;
  why: string;
  parameters: string[];
  metrics: ExperimentMetric[];
  logs?: string[];
};

export const runSummary = {
  repository: "aero-nav/autonomy",
  pullRequest: "#428",
  branch: "feature/waypoint-speed-ceiling",
  title: "Increase maximum waypoint velocity",
  author: "maya.chen",
  commit: "7f3c91a",
  startedAt: "11:42 AM",
  status: "Request changes",
  riskLevel: "High",
  confidence: "89%",
  elapsed: "12m 18s",
  changedFiles: ["navigator/mission_params.cpp", "src/nav/path_tracker.cpp", "config/mission_limits.yaml"],
  diffSummary: "MAX_WP_VELOCITY raised from 8 m/s to 14 m/s in mission-following code.",
  reviewDraft:
    "Generated three validation experiments based on the modified navigation code. Recommendation: request changes. Crosswind mission exceeded acceptable tracking error."
};

export const reasoningSteps = [
  "Parsed diff and isolated a mission-following parameter change in navigator/.",
  "Detected MAX_WP_VELOCITY raised 8 -> 14 m/s (+75%).",
  "Mapped the change to waypoint tracking, turn overshoot, and estimator recovery risks.",
  "Selected scenarios that stress tracking under wind, degraded GPS, and dense waypoint spacing.",
  "Holding GitHub review until the running simulation finishes and artifacts are attached."
];

export const selectedTemplates = [
  { label: "crosswind", selected: true },
  { label: "gps degradation", selected: true },
  { label: "dense waypoints", selected: true },
  { label: "tight turns", selected: false },
  { label: "low-battery rtl", selected: false }
];

export const experiments: Experiment[] = [
  {
    id: "exp-crosswind",
    name: "Crosswind + Tight Turns",
    template: "crosswind_turns",
    icon: Wind,
    status: "failed",
    progress: 100,
    elapsed: "2:44",
    why: "Higher waypoint velocity reduces turn-settling time, so the run validates lateral tracking under a steady crosswind.",
    parameters: ["wind 9 m/s lateral", "turn radius 18 m", "max speed 14 m/s"],
    metrics: [
      { label: "cross-track error", value: "4.8 m", limit: "<= 2.5 m", state: "bad" },
      { label: "overshoot", value: "7.2 m", limit: "<= 5.0 m", state: "bad" },
      { label: "completion", value: "100%", limit: "100%", state: "ok" }
    ],
    logs: [
      "[t+0.2s] spawn px4 sitl + gazebo · world=crosswind_turns",
      "[t+18.6s] wind field stable · 9.0 m/s lateral",
      "[t+73.1s] cross-track error 4.8 m · threshold 2.5 m",
      "[t+164.0s] result failed · artifact bundle ready"
    ]
  },
  {
    id: "exp-gps",
    name: "GPS Degradation",
    template: "gps_degraded",
    icon: Radio,
    status: "running",
    progress: 64,
    elapsed: "1:16",
    why: "Faster transit shortens the window to recover from position error, so the agent checks navigation robustness with degraded GPS.",
    parameters: ["noise sigma 1.5 m", "dropout 2.0 s", "40 waypoints"],
    metrics: [
      { label: "cross-track error", value: "0.6 m", limit: "<= 2.5 m", state: "ok" },
      { label: "estimator drift", value: "1.1 m", limit: "<= 2.0 m", state: "ok" },
      { label: "remaining", value: "14 legs", limit: "0", state: "warn" }
    ],
    logs: [
      "[t+0.2s] spawn px4 sitl + gazebo · world=gps_degraded",
      "[t+1.1s] mission upload ok · 40 waypoints",
      "[t+2.4s] inject gps noise sigma=1.5m",
      "[t+41.8s] dropout window 2.0s @ wp12",
      "[t+52.6s] cross-track err 0.58m · nominal"
    ]
  },
  {
    id: "exp-dense-waypoints",
    name: "Dense Waypoint Mission",
    template: "dense_waypoints",
    icon: Route,
    status: "queued",
    progress: 0,
    elapsed: "est. 2:10",
    why: "Short segment lengths leave little room to correct at higher speed, stressing waypoint acceptance and controller stability.",
    parameters: ["40 waypoints", "3 m spacing", "inspection mission"],
    metrics: [
      { label: "acceptance misses", value: "pending", limit: "0", state: "warn" },
      { label: "mean error", value: "pending", limit: "<= 2.5 m", state: "warn" },
      { label: "runner", value: "ready", limit: "PX4 VPS", state: "ok" }
    ]
  }
];

export const reviewChecks = [
  { label: "Agent plan", value: "5 steps", icon: ShieldCheck },
  { label: "Simulation runner", value: "PX4 VPS online", icon: CircleGauge },
  { label: "PR context", value: "Diff synced", icon: GitPullRequest },
  { label: "Flight envelope", value: "1 breach", icon: AlertTriangle },
  { label: "Mission templates", value: "3 selected", icon: Navigation },
  { label: "Failsafe coverage", value: "Not selected", icon: BatteryWarning }
];
