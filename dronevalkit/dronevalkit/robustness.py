"""Cross-algorithm robustness summaries for experiment-suite outputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import csv
import json
import math
import os
from statistics import mean
from typing import Iterable

from .analysis import StatisticalReport


@dataclass(frozen=True)
class AlgorithmRobustnessRow:
    """Paper-facing robustness summary for one algorithm."""

    algorithm_label: str
    scenario_count: int
    run_count: int
    baseline_run_count: int
    stressed_run_count: int
    baseline_time_inflation: float | None
    stressed_time_inflation: float | None
    baseline_makespan_inflation: float | None
    stressed_makespan_inflation: float | None
    baseline_feasible_sortie_rate: float | None
    stressed_feasible_sortie_rate: float | None
    mean_time_inflation_delta: float | None
    mean_makespan_inflation_delta: float | None
    mean_feasible_sortie_rate_delta: float | None
    robustness_score: float | None
    robustness_rank: int = 0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class AlgorithmRobustnessReport:
    """Generate ranking tables and plots from suite-level run rows."""

    def __init__(
        self,
        rows: Iterable[dict[str, object]],
        *,
        baseline_scenario_id: str = "baseline",
    ):
        self.baseline_scenario_id = str(baseline_scenario_id)
        self._rows = self._normalized_rows(rows)
        self._stats = StatisticalReport(self._rows)

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        *,
        baseline_scenario_id: str = "baseline",
    ) -> AlgorithmRobustnessReport:
        with Path(path).open(newline="", encoding="utf-8") as handle:
            return cls(csv.DictReader(handle), baseline_scenario_id=baseline_scenario_id)

    def analysis_scope(self) -> str:
        scenario_ids = {str(row.get("scenario_id", "")) for row in self._rows}
        if self.baseline_scenario_id in scenario_ids and len(scenario_ids) > 1:
            return "stressed_only"
        return "all_scenarios"

    def summary_rows(self) -> list[AlgorithmRobustnessRow]:
        if not self._rows:
            return []

        algorithm_labels = sorted({str(row.get("algorithm_label", "")) for row in self._rows if row.get("algorithm_label")})
        per_metric = {
            metric: self._summary_map(metric)
            for metric in ("mean_time_inflation", "makespan_inflation", "feasible_sortie_rate")
        }
        per_metric_delta = {
            metric: self._paired_delta_map(metric)
            for metric in ("mean_time_inflation", "makespan_inflation", "feasible_sortie_rate")
        }

        scope = self.analysis_scope()
        summary_rows: list[AlgorithmRobustnessRow] = []
        for algorithm_label in algorithm_labels:
            algorithm_rows = [row for row in self._rows if str(row.get("algorithm_label", "")) == algorithm_label]
            scenario_ids = sorted({str(row.get("scenario_id", "")) for row in algorithm_rows if row.get("scenario_id")})
            analysis_scenarios = [
                scenario_id
                for scenario_id in scenario_ids
                if scope != "stressed_only" or scenario_id != self.baseline_scenario_id
            ]
            analysis_rows = [
                row
                for row in algorithm_rows
                if str(row.get("scenario_id", "")) in analysis_scenarios
            ]
            baseline_rows = [
                row
                for row in algorithm_rows
                if str(row.get("scenario_id", "")) == self.baseline_scenario_id
            ]

            baseline_time = per_metric["mean_time_inflation"].get((algorithm_label, self.baseline_scenario_id))
            baseline_makespan = per_metric["makespan_inflation"].get((algorithm_label, self.baseline_scenario_id))
            baseline_feasible = per_metric["feasible_sortie_rate"].get((algorithm_label, self.baseline_scenario_id))
            stressed_time = self._average_metric(per_metric["mean_time_inflation"], algorithm_label, analysis_scenarios)
            stressed_makespan = self._average_metric(per_metric["makespan_inflation"], algorithm_label, analysis_scenarios)
            stressed_feasible = self._average_metric(per_metric["feasible_sortie_rate"], algorithm_label, analysis_scenarios)

            score_components = [
                abs(stressed_time - 1.0) if stressed_time is not None else None,
                abs(stressed_makespan - 1.0) if stressed_makespan is not None else None,
                (1.0 - stressed_feasible) if stressed_feasible is not None else None,
            ]
            score_values = [component for component in score_components if component is not None]
            robustness_score = mean(score_values) if score_values else None

            summary_rows.append(
                AlgorithmRobustnessRow(
                    algorithm_label=algorithm_label,
                    scenario_count=len(scenario_ids),
                    run_count=len(algorithm_rows),
                    baseline_run_count=len(baseline_rows),
                    stressed_run_count=len(analysis_rows),
                    baseline_time_inflation=baseline_time,
                    stressed_time_inflation=stressed_time,
                    baseline_makespan_inflation=baseline_makespan,
                    stressed_makespan_inflation=stressed_makespan,
                    baseline_feasible_sortie_rate=baseline_feasible,
                    stressed_feasible_sortie_rate=stressed_feasible,
                    mean_time_inflation_delta=per_metric_delta["mean_time_inflation"].get(algorithm_label),
                    mean_makespan_inflation_delta=per_metric_delta["makespan_inflation"].get(algorithm_label),
                    mean_feasible_sortie_rate_delta=per_metric_delta["feasible_sortie_rate"].get(algorithm_label),
                    robustness_score=robustness_score,
                )
            )

        ranked_rows = sorted(
            summary_rows,
            key=lambda row: (
                math.inf if row.robustness_score is None else row.robustness_score,
                -(row.stressed_feasible_sortie_rate if row.stressed_feasible_sortie_rate is not None else -math.inf),
                math.inf if row.stressed_time_inflation is None else row.stressed_time_inflation,
                math.inf if row.stressed_makespan_inflation is None else row.stressed_makespan_inflation,
                row.algorithm_label,
            ),
        )
        return [
            replace(row, robustness_rank=index + 1)
            for index, row in enumerate(ranked_rows)
        ]

    def delta_rows(self) -> list[dict[str, object]]:
        if not self._rows:
            return []
        scenario_ids = {str(row.get("scenario_id", "")) for row in self._rows}
        if self.baseline_scenario_id not in scenario_ids or len(scenario_ids) < 2:
            return []

        rows: list[dict[str, object]] = []
        for metric in ("mean_time_inflation", "makespan_inflation", "feasible_sortie_rate"):
            paired_rows = self._stats.paired_test_rows(
                metric,
                compare_column="scenario_id",
                baseline_value=self.baseline_scenario_id,
                pair_by=("case_id", "replication"),
                group_by=("algorithm_label",),
            )
            for row in paired_rows:
                rows.append(row.as_dict())
        return rows

    def to_summary_csv(self, path: str | Path) -> Path:
        rows = [row.as_dict() for row in self.summary_rows()]
        path = Path(path)
        self._ensure_parent_dir(path)
        if not rows:
            path.write_text("", encoding="utf-8")
            return path
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def to_delta_csv(self, path: str | Path) -> Path:
        rows = self.delta_rows()
        path = Path(path)
        self._ensure_parent_dir(path)
        if not rows:
            path.write_text("", encoding="utf-8")
            return path
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def to_summary_latex(
        self,
        path: str | Path,
        *,
        caption: str = "Algorithm robustness ranking across simulated scenarios.",
        label: str = "tab:algorithm-robustness-ranking",
    ) -> Path:
        rows = self.summary_rows()
        lines = [
            "\\begin{table}[t]",
            "\\centering",
            f"\\caption{{{StatisticalReport._latex_escape(caption)}}}",
            f"\\label{{{StatisticalReport._latex_escape(label)}}}",
            "\\begin{tabular}{lrrrrrr}",
            "\\hline",
            "Rank & Algorithm & Runs & Time Infl. & Makespan Infl. & Feasible Rate & Score \\\\",
            "\\hline",
        ]
        for row in rows:
            lines.append(
                " & ".join(
                    [
                        str(row.robustness_rank),
                        StatisticalReport._latex_escape(row.algorithm_label),
                        str(row.stressed_run_count),
                        self._fmt_number(row.stressed_time_inflation),
                        self._fmt_number(row.stressed_makespan_inflation),
                        self._fmt_number(row.stressed_feasible_sortie_rate),
                        self._fmt_number(row.robustness_score),
                    ]
                )
                + " \\\\"
            )
        lines.extend(["\\hline", "\\end{tabular}", "\\end{table}"])

        path = Path(path)
        self._ensure_parent_dir(path)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def plot_ranking(self, path: str | Path) -> Path:
        os.environ.setdefault("MPLCONFIGDIR", "/tmp/dronevalkit-mpl")
        import matplotlib.pyplot as plt

        rows = self.summary_rows()
        path = Path(path)
        self._ensure_parent_dir(path)

        fig, ax = plt.subplots(figsize=(7.5, max(3.0, 1.1 + (0.7 * max(1, len(rows))))))
        if not rows:
            ax.text(0.5, 0.5, "No completed run rows available", ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
            fig.savefig(path, bbox_inches="tight")
            plt.close(fig)
            return path

        labels = [row.algorithm_label for row in rows]
        scores = [row.robustness_score if row.robustness_score is not None else 0.0 for row in rows]
        feasible_rates = [
            row.stressed_feasible_sortie_rate if row.stressed_feasible_sortie_rate is not None else 0.0
            for row in rows
        ]
        colors = [plt.cm.viridis(max(0.0, min(1.0, rate))) for rate in feasible_rates]

        y_positions = list(range(len(rows)))
        ax.barh(y_positions, scores, color=colors, edgecolor="black", linewidth=0.6)
        ax.set_yticks(y_positions, labels=labels)
        ax.invert_yaxis()
        ax.set_xlabel("Robustness Score (lower is better)")
        ax.set_title("Cross-Algorithm Robustness Ranking")
        ax.grid(axis="x", alpha=0.25)

        for y_position, row in zip(y_positions, rows):
            annotation = (
                f"rank {row.robustness_rank}, "
                f"time={self._fmt_number(row.stressed_time_inflation)}, "
                f"feas={self._fmt_number(row.stressed_feasible_sortie_rate)}"
            )
            x_position = (row.robustness_score if row.robustness_score is not None else 0.0) + 0.01
            ax.text(x_position, y_position, annotation, va="center", fontsize=9)

        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path

    def write_artifacts(self, output_dir: str | Path) -> dict[str, object]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = self.to_summary_csv(output_dir / "summary.csv")
        delta_path = self.to_delta_csv(output_dir / "paired_deltas.csv")
        latex_path = self.to_summary_latex(output_dir / "summary.tex")
        figure_path = self.plot_ranking(output_dir / "ranking.pdf")
        payload = {
            "output_dir": str(output_dir),
            "algorithm_count": len(self.summary_rows()),
            "analysis_scope": self.analysis_scope(),
            "baseline_scenario_id": self.baseline_scenario_id,
            "summary_csv": str(summary_path),
            "paired_deltas_csv": str(delta_path),
            "summary_latex": str(latex_path),
            "ranking_plot": str(figure_path),
        }
        (output_dir / "summary.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        return payload

    def _summary_map(self, metric: str) -> dict[tuple[str, str], float]:
        rows = self._stats.metric_summary_rows(
            metric,
            group_by=("algorithm_label", "scenario_id"),
        )
        return {
            (str(row.group_values["algorithm_label"]), str(row.group_values["scenario_id"])): row.mean
            for row in rows
        }

    def _paired_delta_map(self, metric: str) -> dict[str, float]:
        scenario_ids = {str(row.get("scenario_id", "")) for row in self._rows}
        if self.baseline_scenario_id not in scenario_ids or len(scenario_ids) < 2:
            return {}
        paired_rows = self._stats.paired_test_rows(
            metric,
            compare_column="scenario_id",
            baseline_value=self.baseline_scenario_id,
            pair_by=("case_id", "replication"),
            group_by=("algorithm_label",),
        )
        values_by_algorithm: dict[str, list[float]] = {}
        for row in paired_rows:
            values_by_algorithm.setdefault(str(row.group_values["algorithm_label"]), []).append(row.mean_delta)
        return {
            algorithm_label: mean(values)
            for algorithm_label, values in values_by_algorithm.items()
            if values
        }

    @staticmethod
    def _average_metric(
        metric_map: dict[tuple[str, str], float],
        algorithm_label: str,
        scenario_ids: Iterable[str],
    ) -> float | None:
        values = [
            metric_map[(algorithm_label, scenario_id)]
            for scenario_id in scenario_ids
            if (algorithm_label, scenario_id) in metric_map
        ]
        if not values:
            return None
        return mean(values)

    @staticmethod
    def _fmt_number(value: float | None) -> str:
        if value is None:
            return "n/a"
        return f"{value:.3f}"

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if value in (None, ""):
            return None
        return float(value)

    @classmethod
    def _normalized_rows(cls, rows: Iterable[dict[str, object]]) -> list[dict[str, object]]:
        normalized_rows: list[dict[str, object]] = []
        for row in rows:
            if str(row.get("status", "")).strip() != "completed":
                continue
            normalized = dict(row)
            planned_makespan = cls._coerce_float(normalized.get("planned_makespan_s"))
            actual_makespan = cls._coerce_float(normalized.get("actual_makespan_s"))
            normalized["makespan_inflation"] = (
                (actual_makespan / planned_makespan)
                if planned_makespan is not None
                and actual_makespan is not None
                and not math.isclose(planned_makespan, 0.0)
                else None
            )
            normalized_rows.append(normalized)
        return normalized_rows

    @staticmethod
    def _ensure_parent_dir(path: Path) -> None:
        parent = os.path.dirname(str(path))
        if parent:
            os.makedirs(parent, exist_ok=True)


def generate_algorithm_robustness_artifacts(
    rows: Iterable[dict[str, object]],
    output_dir: str | Path,
    *,
    baseline_scenario_id: str = "baseline",
) -> dict[str, object]:
    """Write ranking artifacts for suite-level algorithm robustness."""

    report = AlgorithmRobustnessReport(rows, baseline_scenario_id=baseline_scenario_id)
    return report.write_artifacts(output_dir)
