"""Lightweight web monitor for experiment-suite outputs."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import mimetypes
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

logger = logging.getLogger(__name__)

_DEFAULT_REFRESH_SECONDS = 5.0


def build_monitor_snapshot(root: str | Path) -> dict[str, Any]:
    """Build a JSON-serializable snapshot for all experiment suites below *root*."""

    root_path = Path(root).expanduser()
    suite_dirs = _discover_suite_dirs(root_path)
    suites = [build_suite_snapshot(root_path, suite_dir) for suite_dir in suite_dirs]
    suites.sort(key=lambda suite: _coerce_float(suite.get("last_updated_ts")) or 0.0, reverse=True)

    totals = {
        "planned_runs": sum(int(suite["planned_runs"]) for suite in suites),
        "completed_runs": sum(int(suite["completed_runs"]) for suite in suites),
        "failed_runs": sum(int(suite["failed_runs"]) for suite in suites),
        "running_runs": sum(int(suite["running_runs"]) for suite in suites),
        "pending_runs": sum(int(suite["pending_runs"]) for suite in suites),
    }

    return {
        "generated_at": _iso_timestamp(),
        "root": str(root_path.resolve()) if root_path.exists() else str(root_path),
        "root_exists": root_path.exists(),
        "suite_count": len(suites),
        "totals": totals,
        "suites": [_strip_sort_keys(suite) for suite in suites],
    }


def build_suite_snapshot(root: str | Path, suite_dir: str | Path) -> dict[str, Any]:
    """Build a JSON-serializable snapshot for one experiment suite."""

    root_path = Path(root).expanduser()
    suite_path = Path(suite_dir).expanduser()
    suite_id = suite_path.relative_to(root_path).as_posix() or "."
    run_plan_rows = _read_csv_rows(suite_path / "run_plan.csv")
    run_result_rows = _read_csv_rows(suite_path / "run_results.csv")
    run_results_by_id = {
        str(row["run_id"]): row
        for row in run_result_rows
        if row.get("run_id")
    }
    status_payloads = _read_status_payloads(suite_path / "raw_runs")
    summary = _read_json(suite_path / "summary.json") or {}

    planned_by_id = {
        str(row["run_id"]): row
        for row in run_plan_rows
        if row.get("run_id")
    }
    run_ids = sorted(set(planned_by_id) | set(run_results_by_id) | set(status_payloads))

    # Per-run `status.json` is authoritative for run state. This matters when a
    # suite is resumed in-place: `run_results.csv` and `summary.json` may only
    # reflect the latest invocation, while `raw_runs/*/status.json` still
    # preserves the terminal state for earlier completed runs.
    runs = [
        _build_run_snapshot(
            root_path=root_path,
            suite_path=suite_path,
            planned_row=planned_by_id.get(run_id, {}),
            run_result_row=run_results_by_id.get(run_id, {}),
            status_entry=status_payloads.get(run_id),
        )
        for run_id in run_ids
    ]
    runs.sort(
        key=lambda run: (
            _coerce_float(run.get("updated_at_ts")) or 0.0,
            str(run.get("run_id", "")),
        ),
        reverse=True,
    )

    completed_runs = sum(1 for run in runs if run["status"] == "completed")
    failed_runs = sum(1 for run in runs if str(run["status"]).startswith("failed"))
    running_runs = sum(1 for run in runs if run["status"] == "running")
    pending_runs = sum(1 for run in runs if run["status"] == "pending")
    planned_runs = len(run_ids)
    recent_failures = [run for run in runs if str(run["status"]).startswith("failed")][:5]
    recent_runs = runs[:10]
    last_updated_ts = max((_coerce_float(run.get("updated_at_ts")) or 0.0) for run in runs) if runs else 0.0

    return {
        "suite_id": suite_id,
        "name": suite_path.name,
        "path": str(suite_path.resolve()),
        "manifest_path": summary.get("manifest_path"),
        "output_dir": summary.get("output_dir") or str(suite_path),
        "correction_model": summary.get("correction_model"),
        "correction_scenario_id": summary.get("correction_scenario_id"),
        "scenarios": summary.get("scenarios", []),
        "planned_runs": planned_runs,
        "completed_runs": completed_runs,
        "failed_runs": failed_runs,
        "running_runs": running_runs,
        "pending_runs": pending_runs,
        "completion_ratio": ((completed_runs + failed_runs) / planned_runs) if planned_runs else 0.0,
        "last_updated_at": _timestamp_from_epoch(last_updated_ts) if last_updated_ts else None,
        "last_updated_ts": last_updated_ts,
        "recent_failures": [_strip_sort_keys(run) for run in recent_failures],
        "recent_runs": [_strip_sort_keys(run) for run in recent_runs],
        "runs": [_strip_sort_keys(run) for run in runs],
    }


def serve_experiment_monitor(
    root: str | Path = "results/experiments",
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    refresh_seconds: float = _DEFAULT_REFRESH_SECONDS,
) -> None:
    """Serve the monitor portal until interrupted."""

    root_path = Path(root).expanduser()
    handler = _build_handler(root_path=root_path, refresh_seconds=refresh_seconds)
    server = ThreadingHTTPServer((host, port), handler)
    display_host = host if host not in {"0.0.0.0", "::"} else "localhost"
    logger.info(
        "Serving experiment monitor for %s at http://%s:%d",
        root_path,
        display_host,
        port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping experiment monitor.")
    finally:
        server.server_close()


def main() -> None:
    """CLI entrypoint for the experiment monitor."""

    parser = argparse.ArgumentParser(
        description="Serve a lightweight web portal for dronevalkit experiment outputs.",
    )
    parser.add_argument(
        "--root",
        default="results/experiments",
        help="Directory containing one or more experiment-suite output folders.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Interface to bind. Use 127.0.0.1 to keep the monitor local-only.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="TCP port to bind.",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=float,
        default=_DEFAULT_REFRESH_SECONDS,
        help="Browser auto-refresh interval for dashboard data.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    serve_experiment_monitor(
        root=args.root,
        host=args.host,
        port=args.port,
        refresh_seconds=float(args.refresh_seconds),
    )


def _discover_suite_dirs(root_path: Path) -> list[Path]:
    if not root_path.exists():
        return []
    return sorted({path.parent for path in root_path.rglob("run_plan.csv")})


def _build_run_snapshot(
    *,
    root_path: Path,
    suite_path: Path,
    planned_row: dict[str, str],
    run_result_row: dict[str, str],
    status_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    status_payload = dict(status_entry["payload"]) if status_entry is not None else {}
    status_path = status_entry["path"] if status_entry is not None else None
    resolved_status = _resolved_status(
        status_payload.get("status"),
        run_result_row.get("status"),
    )
    run_dir = status_path.parent if status_path is not None else suite_path / "raw_runs" / str(
        _first_nonempty(planned_row.get("run_id"), run_result_row.get("run_id"), status_payload.get("run_id"), "")
    )

    updated_at = _first_nonempty(
        status_payload.get("updated_at"),
        run_result_row.get("updated_at"),
        _timestamp_from_epoch(status_path.stat().st_mtime) if status_path is not None and status_path.exists() else None,
    )
    updated_at_ts = _first_nonempty(
        status_payload.get("updated_at_ts"),
        _coerce_float(run_result_row.get("updated_at_ts")),
        status_path.stat().st_mtime if status_path is not None and status_path.exists() else None,
    )

    return {
        "run_id": _first_nonempty(planned_row.get("run_id"), run_result_row.get("run_id"), status_payload.get("run_id"), ""),
        "case_id": _first_nonempty(planned_row.get("case_id"), run_result_row.get("case_id"), status_payload.get("case_id")),
        "scenario_id": _first_nonempty(planned_row.get("scenario_id"), run_result_row.get("scenario_id"), status_payload.get("scenario_id")),
        "scenario_label": _first_nonempty(planned_row.get("scenario_label"), run_result_row.get("scenario_label"), status_payload.get("scenario_label")),
        "replication": _coerce_int(_first_nonempty(planned_row.get("replication"), run_result_row.get("replication"), status_payload.get("replication"))),
        "benchmark_family": _first_nonempty(planned_row.get("benchmark_family"), run_result_row.get("benchmark_family"), status_payload.get("benchmark_family")),
        "algorithm_label": _first_nonempty(planned_row.get("algorithm_label"), run_result_row.get("algorithm_label"), status_payload.get("algorithm_label")),
        "source_path": _first_nonempty(planned_row.get("source_path"), run_result_row.get("source_path"), status_payload.get("source_path")),
        "output_dir": _first_nonempty(planned_row.get("output_dir"), run_result_row.get("output_dir"), status_payload.get("output_dir")),
        "wind_speed": _coerce_float(planned_row.get("wind_speed")),
        "wind_direction": _coerce_float(planned_row.get("wind_direction")),
        "battery_longevity": _coerce_float(planned_row.get("battery_longevity")),
        "altitude": _coerce_float(planned_row.get("altitude")),
        "speed_factor": _coerce_float(planned_row.get("speed_factor")),
        "sortie_count": _coerce_int(_first_nonempty(planned_row.get("sortie_count"), run_result_row.get("sortie_count"), status_payload.get("sortie_count"))),
        "status": resolved_status,
        "error": _first_nonempty(status_payload.get("error"), run_result_row.get("error"), ""),
        "sortie_row_count": _coerce_int(_first_nonempty(status_payload.get("sortie_row_count"), run_result_row.get("sortie_row_count"))),
        "planned_makespan_s": _coerce_float(_first_nonempty(status_payload.get("planned_makespan_s"), run_result_row.get("planned_makespan_s"), planned_row.get("planned_makespan_s"))),
        "actual_makespan_s": _coerce_float(_first_nonempty(status_payload.get("actual_makespan_s"), run_result_row.get("actual_makespan_s"))),
        "raw_makespan_s": _coerce_float(_first_nonempty(status_payload.get("raw_makespan_s"), run_result_row.get("raw_makespan_s"))),
        "mean_time_inflation": _coerce_float(_first_nonempty(status_payload.get("mean_time_inflation"), run_result_row.get("mean_time_inflation"))),
        "corrected_mean_time_inflation": _coerce_float(run_result_row.get("corrected_mean_time_inflation")),
        "feasible_sortie_rate": _coerce_float(_first_nonempty(status_payload.get("feasible_sortie_rate"), run_result_row.get("feasible_sortie_rate"))),
        "duration_s": _coerce_float(_first_nonempty(status_payload.get("duration_s"), run_result_row.get("duration_s"))),
        "started_at": _first_nonempty(status_payload.get("started_at"), run_result_row.get("started_at")),
        "finished_at": _first_nonempty(status_payload.get("finished_at"), run_result_row.get("finished_at")),
        "updated_at": updated_at,
        "updated_at_ts": _coerce_float(updated_at_ts),
        "artifacts": _artifact_urls(root_path, run_dir),
    }


def _artifact_urls(root_path: Path, run_dir: Path) -> dict[str, str]:
    artifact_files = {
        "status": run_dir / "status.json",
        "summary": run_dir / "run_summary.json",
        "experiment_log": run_dir / "experiment.log",
        "sorties_csv": run_dir / "sortie_rows.csv",
        "planned_route": run_dir / "planned_route.png",
        "gantt": run_dir / "gantt.png",
        "gantt_corrected": run_dir / "gantt_corrected.png",
    }
    artifacts: dict[str, str] = {}
    for name, path in artifact_files.items():
        if path.exists():
            artifacts[name] = _file_url(root_path, path)
    for log_path in sorted(run_dir.glob("simulation/**/*.log")):
        relative_name = _log_artifact_name(run_dir, log_path)
        artifacts[relative_name] = _file_url(root_path, log_path)
    return artifacts


def _log_artifact_name(run_dir: Path, log_path: Path) -> str:
    relative = log_path.relative_to(run_dir)
    if relative.name == "px4_stdout.log":
        parts = relative.parts
        for part in parts:
            if part.startswith("instance_"):
                return f"px4_stdout_{part.removeprefix('instance_')}"
        return "px4_stdout"
    stem = "_".join(relative.with_suffix("").parts)
    return stem.replace("/", "_")


def _read_status_payloads(raw_runs_dir: Path) -> dict[str, dict[str, Any]]:
    if not raw_runs_dir.exists():
        return {}
    payloads: dict[str, dict[str, Any]] = {}
    for run_dir in sorted(path for path in raw_runs_dir.iterdir() if path.is_dir()):
        status_path = run_dir / "status.json"
        payload = _read_json(status_path)
        if payload is None:
            continue
        payloads[run_dir.name] = {
            "path": status_path,
            "payload": payload,
        }
    return payloads


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse JSON file %s: %s", path, exc)
        return None


def _build_handler(*, root_path: Path, refresh_seconds: float):
    class MonitorHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._write_html(_dashboard_html(refresh_seconds=refresh_seconds))
                return
            if parsed.path == "/api/overview":
                self._write_json(build_monitor_snapshot(root_path))
                return
            if parsed.path.startswith("/files/"):
                relative_path = unquote(parsed.path.removeprefix("/files/"))
                self._write_file(root_path, Path(relative_path))
                return
            if parsed.path == "/health":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            logger.info("%s - %s", self.address_string(), format % args)

        def _write_html(self, html: str) -> None:
            encoded = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _write_json(self, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _write_file(self, root: Path, relative_path: Path) -> None:
            requested_path = (root / relative_path).resolve()
            try:
                requested_path.relative_to(root.resolve())
            except ValueError:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not requested_path.exists() or not requested_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content = requested_path.read_bytes()
            mime_type, _ = mimetypes.guess_type(str(requested_path))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return MonitorHandler


def _dashboard_html(*, refresh_seconds: float) -> str:
    refresh_ms = max(int(refresh_seconds * 1000), 1000)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>dronevalkit Monitor</title>
  <style>
    :root {{
      --bg: #f3efe5;
      --panel: #fffdfa;
      --ink: #1d1d1b;
      --muted: #6f6a60;
      --line: #d9d1c1;
      --accent: #0e6d68;
      --ok: #2c7a43;
      --warn: #ad7a11;
      --bad: #a43c32;
      --run: #2d5f9a;
      --shadow: 0 12px 32px rgba(36, 31, 22, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Avenir Next", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(14, 109, 104, 0.10), transparent 28rem),
        linear-gradient(180deg, #f7f3ea 0%, var(--bg) 100%);
    }}
    main {{
      width: min(1400px, calc(100vw - 2rem));
      margin: 0 auto;
      padding: 1.25rem 0 3rem;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: flex-end;
      margin-bottom: 1rem;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(2rem, 4vw, 3.5rem);
      line-height: 0.95;
      letter-spacing: -0.04em;
    }}
    .subtitle {{
      color: var(--muted);
      max-width: 48rem;
      margin-top: 0.5rem;
    }}
    .meta {{
      text-align: right;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 0.85rem;
      margin: 1rem 0 1.25rem;
    }}
    .card, details {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
    }}
    .stat {{
      padding: 1rem 1.1rem;
    }}
    .stat-label {{
      display: block;
      color: var(--muted);
      font-size: 0.9rem;
      margin-bottom: 0.35rem;
    }}
    .stat-value {{
      font-size: 1.8rem;
      font-weight: 700;
      letter-spacing: -0.03em;
    }}
    .suite-list {{
      display: grid;
      gap: 1rem;
    }}
    details {{
      overflow: hidden;
    }}
    summary {{
      list-style: none;
      cursor: pointer;
      padding: 1rem 1.2rem;
    }}
    summary::-webkit-details-marker {{ display: none; }}
    .suite-head {{
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: flex-start;
    }}
    .suite-title {{
      margin: 0;
      font-size: 1.2rem;
    }}
    .suite-path {{
      color: var(--muted);
      font-size: 0.92rem;
      margin-top: 0.3rem;
      word-break: break-word;
    }}
    .pill-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
      margin-top: 0.7rem;
    }}
    .pill {{
      border-radius: 999px;
      padding: 0.2rem 0.6rem;
      font-size: 0.82rem;
      border: 1px solid var(--line);
      background: #faf7f0;
    }}
    .status {{
      color: white;
      border: none;
    }}
    .status-completed {{ background: var(--ok); }}
    .status-running {{ background: var(--run); }}
    .status-pending {{ background: var(--warn); }}
    .status-failed {{ background: var(--bad); }}
    .progress {{
      margin-top: 0.85rem;
      height: 10px;
      border-radius: 999px;
      background: #ede4d5;
      overflow: hidden;
    }}
    .progress > span {{
      display: block;
      height: 100%;
      background: linear-gradient(90deg, var(--accent), #e0992d);
    }}
    .suite-body {{
      border-top: 1px solid var(--line);
      padding: 1rem 1.2rem 1.2rem;
    }}
    .suite-columns {{
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 1rem;
    }}
    .mini-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 0.7rem;
    }}
    .mini-card {{
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 0.8rem;
      background: #fcfaf5;
    }}
    .mini-label {{
      color: var(--muted);
      font-size: 0.85rem;
    }}
    .mini-value {{
      display: block;
      margin-top: 0.2rem;
      font-weight: 700;
      font-size: 1.1rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
    }}
    th, td {{
      text-align: left;
      padding: 0.6rem 0.55rem;
      border-bottom: 1px solid #ece5d7;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
    }}
    .run-links a {{
      color: var(--accent);
      text-decoration: none;
      margin-right: 0.55rem;
      white-space: nowrap;
    }}
    .log-toggle {{
      margin-top: 0.45rem;
    }}
    .log-toggle > summary {{
      padding: 0;
      color: var(--accent);
      font-weight: 600;
      cursor: pointer;
    }}
    .log-frame {{
      margin-top: 0.55rem;
      max-height: 22rem;
      overflow: auto;
      background: #171717;
      color: #f4f1ea;
      border-radius: 12px;
      padding: 0.8rem;
      font: 0.8rem/1.45 "IBM Plex Mono", "SFMono-Regular", monospace;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    .error {{
      color: var(--bad);
      max-width: 28rem;
      word-break: break-word;
    }}
    .empty {{
      padding: 1rem 1.2rem;
      color: var(--muted);
    }}
    @media (max-width: 720px) {{
      header {{
        flex-direction: column;
        align-items: flex-start;
      }}
      .meta {{
        text-align: left;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>dronevalkit monitor</h1>
        <div class="subtitle">Live view over experiment-suite output folders, driven directly from <code>run_plan.csv</code> and per-run <code>status.json</code>.</div>
      </div>
      <div class="meta">
        <div id="generated-at">Loading…</div>
        <div>Auto-refresh every {refresh_seconds:.1f}s</div>
      </div>
    </header>
    <section id="app"></section>
  </main>
  <script>
    const REFRESH_MS = {refresh_ms};
    const MONITOR_TZ = "America/Chicago";
    const MONITOR_TZ_LABEL = "US Central";
    const TIMESTAMP_FORMATTER = new Intl.DateTimeFormat("en-US", {{
      timeZone: MONITOR_TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZoneName: "short",
    }});

    function esc(value) {{
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }}

    function fmt(value, digits = 2) {{
      if (value === null || value === undefined || value === "") {{
        return "—";
      }}
      const number = Number(value);
      return Number.isFinite(number) ? number.toFixed(digits) : esc(value);
    }}

    function fmtPct(value) {{
      if (value === null || value === undefined || value === "") {{
        return "—";
      }}
      const number = Number(value);
      return Number.isFinite(number) ? `${{(number * 100).toFixed(0)}}%` : esc(value);
    }}

    function fmtTime(value) {{
      if (value === null || value === undefined || value === "") {{
        return "—";
      }}
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) {{
        return esc(value);
      }}
      return TIMESTAMP_FORMATTER.format(date);
    }}

    function statusClass(status) {{
      if (!status) return "status-pending";
      if (status === "completed") return "status-completed";
      if (status === "running") return "status-running";
      if (String(status).startsWith("failed")) return "status-failed";
      return "status-pending";
    }}

    function renderLinks(artifacts) {{
      const items = Object.entries(artifacts || {{}});
      if (!items.length) {{
        return "—";
      }}
      return `<span class="run-links">${{items.map(([name, url]) => `<a href="${{esc(url)}}" target="_blank" rel="noreferrer">${{esc(name)}}</a>`).join("")}}</span>`;
    }}

    function logKey(suiteId, run) {{
      return `${{suiteId}}::${{String(run.run_id || "")}}`;
    }}

    function renderLogViewer(run, suiteId) {{
      const logUrl = run.artifacts?.experiment_log;
      if (!logUrl) {{
        return "";
      }}
      const key = logKey(suiteId, run);
      return `
        <details class="log-toggle" data-log-key="${{esc(key)}}" data-log-url="${{esc(logUrl)}}"${{openLogs.has(key) ? " open" : ""}}>
          <summary>show log</summary>
          <pre class="log-frame">Loading log…</pre>
        </details>
      `;
    }}

    function renderRuns(runs, suiteId) {{
      if (!runs.length) {{
        return `<div class="empty">No planned runs were found in this suite.</div>`;
      }}
      return `
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Status</th>
                <th>Scenario</th>
                <th>Algorithm</th>
                <th>Duration</th>
                <th>Inflation</th>
                <th>Feasible</th>
                <th>Updated</th>
                <th>Artifacts</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              ${{runs.map((run) => `
                <tr>
                  <td><strong>${{esc(run.run_id)}}</strong><br><span class="suite-path">${{esc(run.case_id || "")}}</span></td>
                  <td><span class="pill status ${{statusClass(run.status)}}">${{esc(run.status)}}</span></td>
                  <td>${{esc(run.scenario_label || run.scenario_id || "—")}}</td>
                  <td>${{esc(run.algorithm_label || "—")}}</td>
                  <td>${{fmt(run.duration_s, 1)}}s</td>
                  <td>${{fmt(run.corrected_mean_time_inflation ?? run.mean_time_inflation, 2)}}</td>
                  <td>${{fmtPct(run.feasible_sortie_rate)}}</td>
                  <td>${{fmtTime(run.updated_at)}}</td>
                  <td>${{renderLinks(run.artifacts)}}${{renderLogViewer(run, suiteId)}}</td>
                  <td class="error">${{esc(run.error || "") || "—"}}</td>
                </tr>
              `).join("")}}
            </tbody>
          </table>
        </div>
      `;
    }}

    const openSuites = new Set();
    const openLogs = new Set();

    function suiteKey(suite) {{
      return String(suite.suite_id || suite.name || "");
    }}

    function captureOpenSuites() {{
      openSuites.clear();
      document.querySelectorAll("details[data-suite-id][open]").forEach((element) => {{
        const suiteId = element.dataset.suiteId;
        if (suiteId) {{
          openSuites.add(suiteId);
        }}
      }});
    }}

    function captureOpenLogs() {{
      openLogs.clear();
      document.querySelectorAll("details[data-log-key][open]").forEach((element) => {{
        const key = element.dataset.logKey;
        if (key) {{
          openLogs.add(key);
        }}
      }});
    }}

    function attachSuiteStateListeners() {{
      document.querySelectorAll("details[data-suite-id]").forEach((element) => {{
        element.addEventListener("toggle", () => {{
          const suiteId = element.dataset.suiteId;
          if (!suiteId) {{
            return;
          }}
          if (element.open) {{
            openSuites.add(suiteId);
          }} else {{
            openSuites.delete(suiteId);
          }}
        }});
      }});
    }}

    async function loadLog(element) {{
      if (!element || element.dataset.loaded === "true") {{
        return;
      }}
      const logFrame = element.querySelector(".log-frame");
      const logUrl = element.dataset.logUrl;
      if (!logFrame || !logUrl) {{
        return;
      }}
      try {{
        const response = await fetch(logUrl, {{ cache: "no-store" }});
        if (!response.ok) {{
          throw new Error(`Log request failed with status ${{response.status}}`);
        }}
        logFrame.textContent = await response.text();
        element.dataset.loaded = "true";
      }} catch (error) {{
        logFrame.textContent = error.message;
      }}
    }}

    function attachLogStateListeners() {{
      document.querySelectorAll("details[data-log-key]").forEach((element) => {{
        if (element.open) {{
          loadLog(element);
        }}
        element.addEventListener("toggle", () => {{
          const key = element.dataset.logKey;
          if (!key) {{
            return;
          }}
          if (element.open) {{
            openLogs.add(key);
            loadLog(element);
          }} else {{
            openLogs.delete(key);
          }}
        }});
      }});
    }}

    function renderSuite(suite) {{
      const suiteId = suiteKey(suite);
      return `
        <details class="card" data-suite-id="${{esc(suiteId)}}"${{openSuites.has(suiteId) ? " open" : ""}}>
          <summary>
            <div class="suite-head">
              <div>
                <h2 class="suite-title">${{esc(suite.name)}}</h2>
                <div class="suite-path">${{esc(suite.path)}}</div>
                <div class="pill-row">
                  <span class="pill">${{suite.planned_runs}} planned</span>
                  <span class="pill">${{suite.completed_runs}} completed</span>
                  <span class="pill">${{suite.running_runs}} running</span>
                  <span class="pill">${{suite.failed_runs}} failed</span>
                  <span class="pill">${{suite.pending_runs}} pending</span>
                </div>
              </div>
              <div class="pill-row">
                <span class="pill">${{esc(suite.correction_model || "no correction")}}</span>
                <span class="pill">${{suite.last_updated_at ? fmtTime(suite.last_updated_at) : "no updates yet"}}</span>
              </div>
            </div>
            <div class="progress"><span style="width:${{Math.round((suite.completion_ratio || 0) * 100)}}%"></span></div>
          </summary>
          <div class="suite-body">
            <div class="suite-columns">
              <div class="mini-grid">
                <div class="mini-card"><span class="mini-label">Manifest</span><span class="mini-value">${{esc(suite.manifest_path || "—")}}</span></div>
                <div class="mini-card"><span class="mini-label">Scenarios</span><span class="mini-value">${{suite.scenarios?.length ?? 0}}</span></div>
                <div class="mini-card"><span class="mini-label">Completion</span><span class="mini-value">${{fmtPct(suite.completion_ratio)}}</span></div>
                <div class="mini-card"><span class="mini-label">Recent failures</span><span class="mini-value">${{suite.recent_failures.length}}</span></div>
              </div>
              ${{suite.recent_failures.length ? `
                <div class="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Failed run</th>
                        <th>Status</th>
                        <th>Updated</th>
                        <th>Error</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${{suite.recent_failures.map((run) => `
                        <tr>
                          <td>${{esc(run.run_id)}}</td>
                          <td><span class="pill status ${{statusClass(run.status)}}">${{esc(run.status)}}</span></td>
                          <td>${{fmtTime(run.updated_at)}}</td>
                          <td class="error">${{esc(run.error || "")}}</td>
                        </tr>
                      `).join("")}}
                    </tbody>
                  </table>
                </div>
              ` : `<div class="empty">No failures recorded for this suite.</div>`}}
              ${{renderRuns(suite.runs, suiteId)}}
            </div>
          </div>
        </details>
      `;
    }}

    async function refresh() {{
      captureOpenSuites();
      captureOpenLogs();
      const response = await fetch("/api/overview", {{ cache: "no-store" }});
      if (!response.ok) {{
        throw new Error(`Monitor request failed with status ${{response.status}}`);
      }}
      const data = await response.json();
      document.getElementById("generated-at").textContent = `Snapshot: ${{fmtTime(data.generated_at)}} (${{MONITOR_TZ_LABEL}})`;
      const suites = data.suites || [];
      const totals = data.totals || {{}};
      const content = `
        <section class="grid">
          <div class="card stat"><span class="stat-label">Root</span><span class="stat-value">${{esc(data.root_exists ? data.root : "missing")}}</span></div>
          <div class="card stat"><span class="stat-label">Suites</span><span class="stat-value">${{data.suite_count ?? 0}}</span></div>
          <div class="card stat"><span class="stat-label">Completed</span><span class="stat-value">${{totals.completed_runs ?? 0}}</span></div>
          <div class="card stat"><span class="stat-label">Running</span><span class="stat-value">${{totals.running_runs ?? 0}}</span></div>
          <div class="card stat"><span class="stat-label">Failed</span><span class="stat-value">${{totals.failed_runs ?? 0}}</span></div>
          <div class="card stat"><span class="stat-label">Pending</span><span class="stat-value">${{totals.pending_runs ?? 0}}</span></div>
        </section>
        <section class="suite-list">
          ${{suites.length ? suites.map(renderSuite).join("") : `<div class="card empty">No experiment suites found below this root yet.</div>`}}
        </section>
      `;
      document.getElementById("app").innerHTML = content;
      attachSuiteStateListeners();
      attachLogStateListeners();
    }}

    async function tick() {{
      try {{
        await refresh();
      }} catch (error) {{
        document.getElementById("app").innerHTML = `<div class="card empty">${{esc(error.message)}}</div>`;
      }}
    }}

    tick();
    setInterval(tick, REFRESH_MS);
  </script>
</body>
</html>
"""


def _strip_sort_keys(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if not key.endswith("_ts")}


def _file_url(root_path: Path, file_path: Path) -> str:
    return "/files/" + quote(file_path.resolve().relative_to(root_path.resolve()).as_posix(), safe="/")


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        return value
    return None


def _resolved_status(status_payload: Any, run_result_status: Any) -> str:
    status_from_payload = _first_nonempty(status_payload)
    status_from_results = _first_nonempty(run_result_status)
    if status_from_payload == "running" and status_from_results not in {None, "running"}:
        return str(status_from_results)
    return str(_first_nonempty(status_from_payload, status_from_results, "pending"))


def _coerce_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _coerce_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    return int(float(value))


def _timestamp_from_epoch(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_timestamp() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
