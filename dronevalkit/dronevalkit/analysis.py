"""Comparison and reporting utilities for planned vs simulated sortie performance."""

from __future__ import annotations

import csv
import functools
import math
import os
from dataclasses import dataclass
from statistics import mean, stdev
from typing import Any, Iterable, Sequence

from .geo import haversine_distance
from .models import Solution


@dataclass
class CorrectionFactors:
    """Derived correction factors to feed back into OR models."""

    time_inflation: dict[str, float]
    leg_time_inflation: dict[str, dict[str, float]]
    energy_multiplier: dict[str, float | None]
    min_safe_margin: float
    distance_inflation: dict[str, float]


@dataclass
class FeasibilityReport:
    """Feasibility summary across all sortie runs."""

    total_sortie_runs: int
    infeasible_count: int
    infeasibility_by_condition: dict[str, float]
    critical_sorties: list[tuple[int, str]]


@dataclass
class _RunRow:
    sortie_index: int
    drone_id: int
    condition: str
    replication: int
    planned_time: float
    actual_time: float
    planned_energy: float | None
    actual_energy: float
    planned_distance: float
    actual_distance: float
    corrected_battery_at_end: float
    feasible: bool


@dataclass
class _LegRunRow:
    sortie_index: int
    drone_id: int
    leg_name: str
    condition: str
    replication: int
    planned_time: float
    actual_time: float


@dataclass
class _LegSummaryRow:
    condition: str
    leg_name: str
    sample_count: int
    planned_time_mean: float
    actual_time_mean: float
    actual_time_std: float
    time_inflation: float


@dataclass
class _PaperLegSummaryRow:
    condition: str
    paper_leg_group: str
    source_legs: tuple[str, ...]
    sample_count: int
    planned_time_mean: float
    actual_time_mean: float
    actual_time_std: float
    time_inflation: float


@dataclass(frozen=True)
class AggregateSummaryRow:
    """Aggregate summary row with confidence interval metadata."""

    group_values: dict[str, object]
    metric: str
    sample_count: int
    mean: float
    std: float
    sem: float
    ci_level: float
    ci_low: float
    ci_high: float
    min_value: float
    max_value: float

    def as_dict(self) -> dict[str, object]:
        return {
            **self.group_values,
            "metric": self.metric,
            "sample_count": self.sample_count,
            "mean": self.mean,
            "std": self.std,
            "sem": self.sem,
            "ci_level": self.ci_level,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "min_value": self.min_value,
            "max_value": self.max_value,
        }


@dataclass(frozen=True)
class PairedTestResult:
    """Paired comparison row for baseline-vs-treatment analysis."""

    group_values: dict[str, object]
    metric: str
    compare_column: str
    baseline_value: object
    comparison_value: object
    pair_count: int
    baseline_mean: float
    comparison_mean: float
    mean_delta: float
    delta_std: float
    delta_sem: float
    ci_level: float
    ci_low: float
    ci_high: float
    t_statistic: float | None
    p_value: float | None
    effect_size_dz: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            **self.group_values,
            "metric": self.metric,
            "compare_column": self.compare_column,
            "baseline_value": self.baseline_value,
            "comparison_value": self.comparison_value,
            "pair_count": self.pair_count,
            "baseline_mean": self.baseline_mean,
            "comparison_mean": self.comparison_mean,
            "mean_delta": self.mean_delta,
            "delta_std": self.delta_std,
            "delta_sem": self.delta_sem,
            "ci_level": self.ci_level,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "t_statistic": self.t_statistic,
            "p_value": self.p_value,
            "effect_size_dz": self.effect_size_dz,
        }


class StatisticalReport:
    """Aggregate statistical analysis over normalized experiment rows."""

    def __init__(self, rows: Iterable[dict[str, object]]):
        self._rows = [dict(row) for row in rows]

    @classmethod
    def from_csv(cls, path: str) -> StatisticalReport:
        with open(path, newline="", encoding="utf-8") as handle:
            return cls(csv.DictReader(handle))

    def metric_summary_rows(
        self,
        metric: str,
        *,
        group_by: Sequence[str] = (),
        ci_level: float = 0.95,
    ) -> list[AggregateSummaryRow]:
        grouped: dict[tuple[tuple[str, object], ...], list[float]] = {}
        for row in self._rows:
            value = self._metric_value(row, metric)
            if value is None:
                continue
            key = tuple((column, row.get(column)) for column in group_by)
            grouped.setdefault(key, []).append(value)

        summary_rows: list[AggregateSummaryRow] = []
        for key in sorted(grouped, key=self._group_sort_key):
            values = grouped[key]
            stats = self._summary_stats(values, ci_level=ci_level)
            summary_rows.append(
                AggregateSummaryRow(
                    group_values=dict(key),
                    metric=metric,
                    sample_count=len(values),
                    mean=stats["mean"],
                    std=stats["std"],
                    sem=stats["sem"],
                    ci_level=ci_level,
                    ci_low=stats["ci_low"],
                    ci_high=stats["ci_high"],
                    min_value=min(values),
                    max_value=max(values),
                )
            )
        return summary_rows

    def paper_summary_rows(
        self,
        metric: str = "time_inflation",
        *,
        group_by: Sequence[str] = ("algorithm_label", "scenario_label", "size_tier"),
        ci_level: float = 0.95,
    ) -> list[AggregateSummaryRow]:
        return self.metric_summary_rows(metric, group_by=group_by, ci_level=ci_level)

    def paired_test_rows(
        self,
        metric: str,
        *,
        compare_column: str,
        baseline_value: object,
        pair_by: Sequence[str],
        group_by: Sequence[str] = (),
        ci_level: float = 0.95,
    ) -> list[PairedTestResult]:
        grouped: dict[tuple[tuple[tuple[str, object], ...], object], dict[tuple[tuple[str, object], ...], float]] = {}
        for row in self._rows:
            value = self._metric_value(row, metric)
            if value is None:
                continue
            compare_value = row.get(compare_column)
            strata = tuple((column, row.get(column)) for column in group_by)
            pair_key = tuple((column, row.get(column)) for column in pair_by)
            bucket = grouped.setdefault((strata, compare_value), {})
            if pair_key in bucket:
                raise ValueError(
                    f"Duplicate paired key for metric '{metric}' in {compare_column}={compare_value!r}: {dict(pair_key)!r}"
                )
            bucket[pair_key] = value

        strata_keys = sorted({strata for strata, _ in grouped}, key=self._group_sort_key)
        test_rows: list[PairedTestResult] = []
        for strata in strata_keys:
            baseline_map = grouped.get((strata, baseline_value))
            if not baseline_map:
                continue
            comparison_values = sorted(
                [compare_value for current_strata, compare_value in grouped if current_strata == strata and compare_value != baseline_value],
                key=self._sort_value,
            )
            for comparison_value in comparison_values:
                comparison_map = grouped[(strata, comparison_value)]
                pair_keys = sorted(set(baseline_map).intersection(comparison_map), key=self._group_sort_key)
                if not pair_keys:
                    continue

                baseline_values = [baseline_map[pair_key] for pair_key in pair_keys]
                comparison_values_list = [comparison_map[pair_key] for pair_key in pair_keys]
                deltas = [comparison - baseline for baseline, comparison in zip(baseline_values, comparison_values_list)]
                test_stats = self._paired_test_stats(deltas, ci_level=ci_level)
                test_rows.append(
                    PairedTestResult(
                        group_values=dict(strata),
                        metric=metric,
                        compare_column=compare_column,
                        baseline_value=baseline_value,
                        comparison_value=comparison_value,
                        pair_count=len(deltas),
                        baseline_mean=mean(baseline_values),
                        comparison_mean=mean(comparison_values_list),
                        mean_delta=test_stats["mean_delta"],
                        delta_std=test_stats["delta_std"],
                        delta_sem=test_stats["delta_sem"],
                        ci_level=ci_level,
                        ci_low=test_stats["ci_low"],
                        ci_high=test_stats["ci_high"],
                        t_statistic=test_stats["t_statistic"],
                        p_value=test_stats["p_value"],
                        effect_size_dz=test_stats["effect_size_dz"],
                    )
                )
        return test_rows

    def to_metric_summary_csv(
        self,
        path: str,
        metric: str,
        *,
        group_by: Sequence[str] = (),
        ci_level: float = 0.95,
    ) -> None:
        rows = self.metric_summary_rows(metric, group_by=group_by, ci_level=ci_level)
        self._ensure_parent_dir(path)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    *group_by,
                    "metric",
                    "sample_count",
                    "mean",
                    "std",
                    "sem",
                    "ci_level",
                    "ci_low",
                    "ci_high",
                    "min_value",
                    "max_value",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row.as_dict())

    def to_metric_summary_latex(
        self,
        path: str,
        metric: str,
        *,
        group_by: Sequence[str] = (),
        ci_level: float = 0.95,
        caption: str = "TODO: statistical summary caption",
        label: str = "tab:TODO-statistical-summary",
    ) -> None:
        rows = self.metric_summary_rows(metric, group_by=group_by, ci_level=ci_level)
        ci_pct = round(ci_level * 100)
        headers = [self._latex_escape(self._column_label(column)) for column in group_by]
        headers.extend(["N", "Mean", "Std", f"{ci_pct}\\% CI"])

        lines = [
            "\\begin{table}[t]",
            "\\centering",
            f"\\caption{{{self._latex_escape(caption)}}}",
            f"\\label{{{self._latex_escape(label)}}}",
            "\\begin{tabular}{" + "l" * len(headers) + "}",
            "\\hline",
            " & ".join(headers) + " \\\\",
            "\\hline",
        ]
        for row in rows:
            line = [self._latex_escape(str(row.group_values.get(column, ""))) for column in group_by]
            line.extend(
                [
                    str(row.sample_count),
                    f"{row.mean:.3f}",
                    f"{row.std:.3f}",
                    f"[{row.ci_low:.3f}, {row.ci_high:.3f}]",
                ]
            )
            lines.append(" & ".join(line) + " \\\\")
        lines.extend(["\\hline", "\\end{tabular}", "\\end{table}"])

        self._ensure_parent_dir(path)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def to_paired_test_csv(
        self,
        path: str,
        metric: str,
        *,
        compare_column: str,
        baseline_value: object,
        pair_by: Sequence[str],
        group_by: Sequence[str] = (),
        ci_level: float = 0.95,
    ) -> None:
        rows = self.paired_test_rows(
            metric,
            compare_column=compare_column,
            baseline_value=baseline_value,
            pair_by=pair_by,
            group_by=group_by,
            ci_level=ci_level,
        )
        self._ensure_parent_dir(path)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    *group_by,
                    "metric",
                    "compare_column",
                    "baseline_value",
                    "comparison_value",
                    "pair_count",
                    "baseline_mean",
                    "comparison_mean",
                    "mean_delta",
                    "delta_std",
                    "delta_sem",
                    "ci_level",
                    "ci_low",
                    "ci_high",
                    "t_statistic",
                    "p_value",
                    "effect_size_dz",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(row.as_dict())

    @staticmethod
    def _column_label(column: str) -> str:
        return column.replace("_", " ").title()

    @staticmethod
    def _metric_value(row: dict[str, object], metric: str) -> float | None:
        metric_sources = {
            "feasible_rate": row.get("feasible"),
            "feasible_sortie_rate": row.get("feasible"),
        }
        raw_value = row.get(metric) if metric in row else metric_sources.get(metric)
        return StatisticalReport._coerce_float(raw_value)

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            lowered = value.strip().lower()
            if not lowered:
                return None
            if lowered == "true":
                return 1.0
            if lowered == "false":
                return 0.0
            try:
                return float(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _group_sort_key(key: tuple[tuple[str, object], ...]) -> tuple[str, ...]:
        return tuple(f"{column}={StatisticalReport._sort_value(value)}" for column, value in key)

    @staticmethod
    def _sort_value(value: object) -> str:
        return "" if value is None else str(value)

    @classmethod
    def _summary_stats(cls, values: Sequence[float], *, ci_level: float) -> dict[str, float]:
        if not values:
            raise ValueError("values must not be empty")
        sample_count = len(values)
        mean_value = mean(values)
        if sample_count == 1:
            return {
                "mean": mean_value,
                "std": 0.0,
                "sem": 0.0,
                "ci_low": mean_value,
                "ci_high": mean_value,
            }
        std_value = stdev(values)
        sem_value = std_value / math.sqrt(sample_count)
        t_crit = cls._t_critical(ci_level=ci_level, degrees_of_freedom=sample_count - 1)
        margin = t_crit * sem_value
        return {
            "mean": mean_value,
            "std": std_value,
            "sem": sem_value,
            "ci_low": mean_value - margin,
            "ci_high": mean_value + margin,
        }

    @classmethod
    def _paired_test_stats(cls, deltas: Sequence[float], *, ci_level: float) -> dict[str, float | None]:
        if not deltas:
            raise ValueError("deltas must not be empty")
        mean_delta = mean(deltas)
        if len(deltas) == 1:
            return {
                "mean_delta": mean_delta,
                "delta_std": 0.0,
                "delta_sem": 0.0,
                "ci_low": mean_delta,
                "ci_high": mean_delta,
                "t_statistic": None,
                "p_value": None,
                "effect_size_dz": None,
            }

        delta_std = stdev(deltas)
        delta_sem = delta_std / math.sqrt(len(deltas))
        t_crit = cls._t_critical(ci_level=ci_level, degrees_of_freedom=len(deltas) - 1)
        margin = t_crit * delta_sem

        if math.isclose(delta_sem, 0.0):
            t_statistic = 0.0 if math.isclose(mean_delta, 0.0) else math.inf
            p_value = 1.0 if math.isclose(mean_delta, 0.0) else 0.0
        else:
            t_statistic = mean_delta / delta_sem
            p_value = 2.0 * (1.0 - cls._student_t_cdf(abs(t_statistic), len(deltas) - 1))

        effect_size = None if math.isclose(delta_std, 0.0) else mean_delta / delta_std
        return {
            "mean_delta": mean_delta,
            "delta_std": delta_std,
            "delta_sem": delta_sem,
            "ci_low": mean_delta - margin,
            "ci_high": mean_delta + margin,
            "t_statistic": t_statistic,
            "p_value": max(0.0, min(1.0, p_value)),
            "effect_size_dz": effect_size,
        }

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def _t_critical(*, ci_level: float, degrees_of_freedom: int) -> float:
        if degrees_of_freedom <= 0:
            return 0.0
        target = 0.5 + (ci_level / 2.0)
        low = 0.0
        high = 1.0
        while StatisticalReport._student_t_cdf(high, degrees_of_freedom) < target:
            high *= 2.0
            if high > 1_000.0:
                break
        for _ in range(60):
            mid = (low + high) / 2.0
            if StatisticalReport._student_t_cdf(mid, degrees_of_freedom) < target:
                low = mid
            else:
                high = mid
        return (low + high) / 2.0

    @staticmethod
    def _student_t_pdf(x: float, degrees_of_freedom: int) -> float:
        numerator = math.exp(
            math.lgamma((degrees_of_freedom + 1.0) / 2.0) - math.lgamma(degrees_of_freedom / 2.0)
        )
        denominator = math.sqrt(degrees_of_freedom * math.pi)
        return (numerator / denominator) * (
            1.0 + (x * x) / degrees_of_freedom
        ) ** (-(degrees_of_freedom + 1.0) / 2.0)

    @staticmethod
    def _student_t_cdf(x: float, degrees_of_freedom: int) -> float:
        if degrees_of_freedom <= 0:
            raise ValueError("degrees_of_freedom must be positive")
        if math.isclose(x, 0.0):
            return 0.5
        if x < 0.0:
            return 1.0 - StatisticalReport._student_t_cdf(-x, degrees_of_freedom)

        upper = float(x)
        intervals = max(256, int(math.ceil(upper * 256.0)))
        if intervals % 2 == 1:
            intervals += 1
        step = upper / intervals
        total = (
            StatisticalReport._student_t_pdf(0.0, degrees_of_freedom)
            + StatisticalReport._student_t_pdf(upper, degrees_of_freedom)
        )
        for index in range(1, intervals):
            weight = 4.0 if index % 2 == 1 else 2.0
            total += weight * StatisticalReport._student_t_pdf(index * step, degrees_of_freedom)
        return min(1.0, 0.5 + (step * total / 3.0))

    @staticmethod
    def _ensure_parent_dir(path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    @staticmethod
    def _latex_escape(text: str) -> str:
        repl = {
            "\\": r"\textbackslash{}",
            "&": r"\&",
            "%": r"\%",
            "$": r"\$",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
            "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
        }
        return "".join(repl.get(ch, ch) for ch in text)


class ComparisonReport:
    """Main analysis output. Compares planned vs actual across all runs."""

    def __init__(self, solution: Solution, results: list[Any]):
        self.solution = solution
        self.results = results
        self._rows = self._build_rows()
        self._leg_rows = self._build_leg_rows()

    def summary(self) -> None:
        """Print a formatted per-sortie summary table grouped by condition."""
        rows = self._rows
        headers = ["Sortie"]
        if self.solution.num_drones > 1:
            headers.append("Drone")
        headers.extend(
            [
                "Condition",
                "Planned Time",
                "Actual Time",
                "Time Inflation",
                "Planned Energy",
                "Actual Energy",
                "Energy Inflation",
            ]
        )

        table_rows: list[list[str]] = []
        grouped = self._group_by_sortie_drone_condition(rows)

        for key in sorted(grouped):
            sortie_index, drone_id, condition = key
            group = grouped[key]

            planned_time = mean([r.planned_time for r in group])
            actual_times = [r.actual_time for r in group]
            actual_time_mu = mean(actual_times)
            actual_time_sd = stdev(actual_times) if len(actual_times) > 1 else 0.0
            time_inflation = self._safe_ratio(actual_time_mu, planned_time)

            planned_energy_values = [r.planned_energy for r in group if r.planned_energy is not None]
            actual_energies = [r.actual_energy for r in group]
            actual_energy_mu = mean(actual_energies)
            actual_energy_sd = stdev(actual_energies) if len(actual_energies) > 1 else 0.0
            energy_inflation = self._mean_ratio(
                [(r.actual_energy, r.planned_energy) for r in group]
            )

            row = [str(sortie_index)]
            if self.solution.num_drones > 1:
                row.append(str(drone_id))
            row.extend(
                [
                    condition,
                    f"{planned_time:.2f}",
                    f"{actual_time_mu:.2f} +/- {actual_time_sd:.2f}",
                    f"{time_inflation:.3f}",
                    f"{mean(planned_energy_values):.2f}" if planned_energy_values else "n/a",
                    f"{actual_energy_mu:.2f} +/- {actual_energy_sd:.2f}",
                    f"{energy_inflation:.3f}" if energy_inflation is not None else "n/a",
                ]
            )
            table_rows.append(row)

        all_planned_t = [r.planned_time for r in rows]
        all_actual_t = [r.actual_time for r in rows]
        all_planned_e = [r.planned_energy for r in rows if r.planned_energy is not None]
        all_actual_e = [r.actual_energy for r in rows]
        all_energy_inflation = self._mean_ratio(
            [(r.actual_energy, r.planned_energy) for r in rows]
        )

        agg_row = ["ALL"]
        if self.solution.num_drones > 1:
            agg_row.append("-")
        agg_row.extend(
            [
                "-",
                f"{mean(all_planned_t):.2f}" if all_planned_t else "0.00",
                (
                    f"{mean(all_actual_t):.2f} +/- "
                    f"{(stdev(all_actual_t) if len(all_actual_t) > 1 else 0.0):.2f}"
                )
                if all_actual_t
                else "0.00 +/- 0.00",
                (
                    f"{mean([self._safe_ratio(r.actual_time, r.planned_time) for r in rows]):.3f}"
                    if rows
                    else "0.000"
                ),
                f"{mean(all_planned_e):.2f}" if all_planned_e else "n/a",
                (
                    f"{mean(all_actual_e):.2f} +/- "
                    f"{(stdev(all_actual_e) if len(all_actual_e) > 1 else 0.0):.2f}"
                )
                if all_actual_e
                else "0.00 +/- 0.00",
                f"{all_energy_inflation:.3f}" if all_energy_inflation is not None else "n/a",
            ]
        )
        table_rows.append(agg_row)

        self._print_table(headers, table_rows)

    def statistical_report(self) -> StatisticalReport:
        """Return a reusable statistical view over normalized sortie rows."""
        return StatisticalReport(self.raw_rows())

    def metric_summary_rows(
        self,
        metric: str = "time_inflation",
        *,
        group_by: Sequence[str] = ("condition",),
        ci_level: float = 0.95,
    ) -> list[AggregateSummaryRow]:
        """Return aggregate metric rows with confidence intervals."""
        return self.statistical_report().metric_summary_rows(
            metric,
            group_by=group_by,
            ci_level=ci_level,
        )

    def paired_condition_test_rows(
        self,
        metric: str = "time_inflation",
        *,
        baseline_condition: str | None = None,
        pair_by: Sequence[str] = ("sortie_index", "drone_id", "replication"),
        group_by: Sequence[str] = (),
        ci_level: float = 0.95,
    ) -> list[PairedTestResult]:
        """Return paired baseline-vs-condition comparisons for one report."""
        conditions = sorted({row.condition for row in self._rows})
        if not conditions:
            return []
        baseline = baseline_condition if baseline_condition is not None else conditions[0]
        return self.statistical_report().paired_test_rows(
            metric,
            compare_column="condition",
            baseline_value=baseline,
            pair_by=pair_by,
            group_by=group_by,
            ci_level=ci_level,
        )

    def feasibility(self) -> FeasibilityReport:
        """Print and return sortie feasibility report."""
        rows = self._rows
        total = len(rows)
        infeasible_rows = [r for r in rows if not r.feasible]
        infeasible_count = len(infeasible_rows)

        by_condition: dict[str, list[_RunRow]] = {}
        for row in rows:
            by_condition.setdefault(row.condition, []).append(row)

        infeasibility_by_condition: dict[str, float] = {}
        for condition, condition_rows in by_condition.items():
            denom = len(condition_rows)
            num = len([r for r in condition_rows if not r.feasible])
            infeasibility_by_condition[condition] = (num / denom) if denom else 0.0

        critical_pairs = sorted({(r.sortie_index, r.condition) for r in infeasible_rows})

        print("Feasibility Report")
        print(f"Total sortie-runs: {total}")
        print(f"Infeasible: {infeasible_count}")
        print(f"Overall infeasibility rate: {((infeasible_count / total) if total else 0.0):.2%}")
        print("Infeasibility by condition:")
        for condition in sorted(infeasibility_by_condition):
            print(f"  - {condition}: {infeasibility_by_condition[condition]:.2%}")

        if critical_pairs:
            print("Failed sortie/condition pairs:")
            for sortie_index, condition in critical_pairs:
                print(f"  - sortie {sortie_index}, condition {condition}")
        else:
            print("Failed sortie/condition pairs: none")

        return FeasibilityReport(
            total_sortie_runs=total,
            infeasible_count=infeasible_count,
            infeasibility_by_condition=infeasibility_by_condition,
            critical_sorties=critical_pairs,
        )

    def correction_factors(self) -> CorrectionFactors:
        """Compute per-condition correction factors."""
        rows = self._rows
        leg_rows = self._leg_summary_rows()
        by_condition: dict[str, list[_RunRow]] = {}
        for row in rows:
            by_condition.setdefault(row.condition, []).append(row)

        time_inflation: dict[str, float] = {}
        leg_time_inflation: dict[str, dict[str, float]] = {}
        energy_multiplier: dict[str, float | None] = {}
        distance_inflation: dict[str, float] = {}

        for condition, condition_rows in by_condition.items():
            time_inflation[condition] = mean(
                [self._safe_ratio(r.actual_time, r.planned_time) for r in condition_rows]
            )
            energy_multiplier[condition] = self._mean_ratio(
                [(r.actual_energy, r.planned_energy) for r in condition_rows]
            )
            distance_inflation[condition] = mean(
                [self._safe_ratio(r.actual_distance, r.planned_distance) for r in condition_rows]
            )

        for row in leg_rows:
            leg_time_inflation.setdefault(row.condition, {})[row.leg_name] = row.time_inflation

        feasible_rows = [r for r in rows if r.feasible]
        min_safe_margin = min([r.corrected_battery_at_end for r in feasible_rows], default=0.0)

        return CorrectionFactors(
            time_inflation=time_inflation,
            leg_time_inflation=leg_time_inflation,
            energy_multiplier=energy_multiplier,
            min_safe_margin=min_safe_margin,
            distance_inflation=distance_inflation,
        )

    def leg_summary(self) -> None:
        """Print planned vs actual per-leg timing summary."""
        summary_rows = self._leg_summary_rows()
        if not summary_rows:
            print("Leg Timing Summary")
            print("No planned/actual leg timing data available.")
            return

        headers = ["Condition", "Leg", "N", "Planned Time", "Actual Time", "Time Inflation"]

        table_rows: list[list[str]] = []
        for row in summary_rows:
            table_rows.append(
                [
                    row.condition,
                    row.leg_name,
                    str(row.sample_count),
                    f"{row.planned_time_mean:.2f}",
                    f"{row.actual_time_mean:.2f} +/- {row.actual_time_std:.2f}",
                    f"{row.time_inflation:.3f}",
                ]
            )

        self._print_table(headers, table_rows)

    def paper_leg_summary(self) -> None:
        """Print grouped per-leg timing summary for paper-facing reporting."""
        summary_rows = self._paper_leg_summary_rows()
        if not summary_rows:
            print("Paper Leg Timing Summary")
            print("No grouped planned/actual leg timing data available.")
            return

        headers = ["Condition", "Group", "Legs", "N", "Planned Time", "Actual Time", "Time Inflation"]
        table_rows: list[list[str]] = []
        for row in summary_rows:
            table_rows.append(
                [
                    row.condition,
                    row.paper_leg_group,
                    ", ".join(row.source_legs),
                    str(row.sample_count),
                    f"{row.planned_time_mean:.2f}",
                    f"{row.actual_time_mean:.2f} +/- {row.actual_time_std:.2f}",
                    f"{row.time_inflation:.3f}",
                ]
            )

        self._print_table(headers, table_rows)

    def to_latex(self, path: str) -> None:
        """Export summary table as a publication-ready LaTeX table."""
        grouped = self._group_by_sortie_drone_condition(self._rows)

        include_drone = self.solution.num_drones > 1
        cols = ["Sortie"]
        if include_drone:
            cols.append("Drone")
        cols.extend(
            [
                "Condition",
                "Planned Time",
                "Actual Time (mean$\\pm$std)",
                "Time Infl.",
                "Planned Energy",
                "Actual Energy (mean$\\pm$std)",
                "Energy Infl.",
            ]
        )

        lines: list[str] = []
        lines.append("\\begin{table}[t]")
        lines.append("\\centering")
        lines.append("\\caption{TODO: comparison summary caption}")
        lines.append("\\label{tab:TODO-comparison-summary}")
        lines.append("\\begin{tabular}{" + "l" * len(cols) + "}")
        lines.append("\\hline")
        lines.append(" & ".join(cols) + " \\\\")
        lines.append("\\hline")

        for key in sorted(grouped):
            sortie_index, drone_id, condition = key
            group = grouped[key]
            planned_time = mean([r.planned_time for r in group])
            actual_times = [r.actual_time for r in group]
            actual_time_mu = mean(actual_times)
            actual_time_sd = stdev(actual_times) if len(actual_times) > 1 else 0.0
            time_infl = self._safe_ratio(actual_time_mu, planned_time)

            planned_energy_values = [r.planned_energy for r in group if r.planned_energy is not None]
            actual_energies = [r.actual_energy for r in group]
            actual_energy_mu = mean(actual_energies)
            actual_energy_sd = stdev(actual_energies) if len(actual_energies) > 1 else 0.0
            energy_infl = self._mean_ratio([(r.actual_energy, r.planned_energy) for r in group])

            row = [str(sortie_index)]
            if include_drone:
                row.append(str(drone_id))
            row.extend(
                [
                    self._latex_escape(condition),
                    f"{planned_time:.2f}",
                    f"{actual_time_mu:.2f} $\\pm$ {actual_time_sd:.2f}",
                    f"{time_infl:.3f}",
                    f"{mean(planned_energy_values):.2f}" if planned_energy_values else "n/a",
                    f"{actual_energy_mu:.2f} $\\pm$ {actual_energy_sd:.2f}",
                    f"{energy_infl:.3f}" if energy_infl is not None else "n/a",
                ]
            )
            lines.append(" & ".join(row) + " \\\\")

        all_rows = self._rows
        agg_row = ["ALL"]
        if include_drone:
            agg_row.append("-")
        agg_row.extend(
            [
                "-",
                f"{mean([r.planned_time for r in all_rows]):.2f}" if all_rows else "0.00",
                (
                    f"{mean([r.actual_time for r in all_rows]):.2f} "
                    f"$\\pm$ {(stdev([r.actual_time for r in all_rows]) if len(all_rows) > 1 else 0.0):.2f}"
                )
                if all_rows
                else "0.00 $\\pm$ 0.00",
                (
                    f"{mean([self._safe_ratio(r.actual_time, r.planned_time) for r in all_rows]):.3f}"
                    if all_rows
                    else "0.000"
                ),
                (
                    f"{mean([r.planned_energy for r in all_rows if r.planned_energy is not None]):.2f}"
                    if any(r.planned_energy is not None for r in all_rows)
                    else "n/a"
                ),
                (
                    f"{mean([r.actual_energy for r in all_rows]):.2f} "
                    f"$\\pm$ {(stdev([r.actual_energy for r in all_rows]) if len(all_rows) > 1 else 0.0):.2f}"
                )
                if all_rows
                else "0.00 $\\pm$ 0.00",
                (
                    f"{energy_inflation:.3f}"
                    if (energy_inflation := self._mean_ratio(
                        [(r.actual_energy, r.planned_energy) for r in all_rows]
                    )) is not None
                    else "n/a"
                ),
            ]
        )
        lines.append("\\hline")
        lines.append(" & ".join(agg_row) + " \\\\")
        lines.append("\\hline")
        lines.append("\\end{tabular}")
        lines.append("\\end{table}")

        self._ensure_parent_dir(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def to_csv(self, path: str) -> None:
        """Export raw per-sortie rows to CSV."""
        self._ensure_parent_dir(path)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "sortie_index",
                    "drone_id",
                    "condition",
                    "replication",
                    "planned_time",
                    "actual_time",
                    "planned_energy",
                    "actual_energy",
                    "actual_distance",
                    "corrected_battery_at_end",
                    "feasible",
                ]
            )
            for row in sorted(
                self._rows,
                key=lambda r: (r.sortie_index, r.drone_id, r.condition, r.replication),
            ):
                writer.writerow(
                    [
                        row.sortie_index,
                        row.drone_id,
                        row.condition,
                        row.replication,
                        row.planned_time,
                        row.actual_time,
                        row.planned_energy,
                        row.actual_energy,
                        row.actual_distance,
                        row.corrected_battery_at_end,
                        row.feasible,
                    ]
                )

    def raw_rows(self) -> list[dict[str, object]]:
        """Return raw per-sortie rows as serializable dictionaries."""
        return [
            {
                "sortie_index": row.sortie_index,
                "drone_id": row.drone_id,
                "condition": row.condition,
                "replication": row.replication,
                "planned_time": row.planned_time,
                "actual_time": row.actual_time,
                "planned_energy": row.planned_energy,
                "actual_energy": row.actual_energy,
                "planned_distance": row.planned_distance,
                "actual_distance": row.actual_distance,
                "corrected_battery_at_end": row.corrected_battery_at_end,
                "feasible": row.feasible,
                "time_inflation": self._safe_ratio(row.actual_time, row.planned_time),
                "energy_inflation": (
                    self._safe_ratio(row.actual_energy, row.planned_energy)
                    if row.planned_energy is not None and not math.isclose(row.planned_energy, 0.0)
                    else None
                ),
                "distance_inflation": self._safe_ratio(row.actual_distance, row.planned_distance),
            }
            for row in sorted(
                self._rows,
                key=lambda r: (r.sortie_index, r.drone_id, r.condition, r.replication),
            )
        ]

    def to_leg_csv(self, path: str) -> None:
        """Export aggregated per-leg timing rows to CSV."""
        self._ensure_parent_dir(path)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "condition",
                    "leg_name",
                    "sample_count",
                    "planned_time_mean",
                    "actual_time_mean",
                    "actual_time_std",
                    "time_inflation",
                ]
            )
            for row in self._leg_summary_rows():
                writer.writerow(
                    [
                        row.condition,
                        row.leg_name,
                        row.sample_count,
                        row.planned_time_mean,
                        row.actual_time_mean,
                        row.actual_time_std,
                        row.time_inflation,
                    ]
                )

    def to_leg_latex(self, path: str) -> None:
        """Export aggregated per-leg timing summary as a LaTeX table."""
        rows = self._leg_summary_rows()

        lines: list[str] = []
        lines.append("\\begin{table}[t]")
        lines.append("\\centering")
        lines.append("\\caption{TODO: per-leg timing inflation caption}")
        lines.append("\\label{tab:TODO-leg-time-inflation}")
        lines.append("\\begin{tabular}{llllll}")
        lines.append("\\hline")
        lines.append("Condition & Leg & N & Planned Time & Actual Time (mean$\\pm$std) & Time Infl. \\\\")
        lines.append("\\hline")

        for row in rows:
            lines.append(
                " & ".join(
                    [
                        self._latex_escape(row.condition),
                        self._latex_escape(row.leg_name),
                        str(row.sample_count),
                        f"{row.planned_time_mean:.2f}",
                        f"{row.actual_time_mean:.2f} $\\pm$ {row.actual_time_std:.2f}",
                        f"{row.time_inflation:.3f}",
                    ]
                )
                + " \\\\"
            )

        lines.append("\\hline")
        lines.append("\\end{tabular}")
        lines.append("\\end{table}")

        self._ensure_parent_dir(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def to_paper_leg_csv(self, path: str) -> None:
        """Export grouped paper-facing leg timing rows to CSV."""
        self._ensure_parent_dir(path)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "condition",
                    "paper_leg_group",
                    "source_legs",
                    "sample_count",
                    "planned_time_mean",
                    "actual_time_mean",
                    "actual_time_std",
                    "time_inflation",
                ]
            )
            for row in self._paper_leg_summary_rows():
                writer.writerow(
                    [
                        row.condition,
                        row.paper_leg_group,
                        ";".join(row.source_legs),
                        row.sample_count,
                        row.planned_time_mean,
                        row.actual_time_mean,
                        row.actual_time_std,
                        row.time_inflation,
                    ]
                )

    def to_paper_leg_latex(self, path: str) -> None:
        """Export grouped paper-facing leg timing summary as a LaTeX table."""
        rows = self._paper_leg_summary_rows()

        lines: list[str] = []
        lines.append("\\begin{table}[t]")
        lines.append("\\centering")
        lines.append("\\caption{TODO: grouped per-leg timing inflation caption}")
        lines.append("\\label{tab:TODO-grouped-leg-time-inflation}")
        lines.append("\\begin{tabular}{lllllll}")
        lines.append("\\hline")
        lines.append(
            "Condition & Group & Legs & N & Planned Time & Actual Time (mean$\\pm$std) & Time Infl. \\\\"
        )
        lines.append("\\hline")

        for row in rows:
            lines.append(
                " & ".join(
                    [
                        self._latex_escape(row.condition),
                        self._latex_escape(row.paper_leg_group),
                        self._latex_escape(", ".join(row.source_legs)),
                        str(row.sample_count),
                        f"{row.planned_time_mean:.2f}",
                        f"{row.actual_time_mean:.2f} $\\pm$ {row.actual_time_std:.2f}",
                        f"{row.time_inflation:.3f}",
                    ]
                )
                + " \\\\"
            )

        lines.append("\\hline")
        lines.append("\\end{tabular}")
        lines.append("\\end{table}")

        self._ensure_parent_dir(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def plot_scatter(self, path: str, metric: str = "time") -> None:
        """Compatibility wrapper for visualization.plot_scatter."""
        from .visualization import plot_scatter

        plot_scatter(self, path, metric=metric)

    def plot_feasibility(self, path: str, threshold: float = 20.0) -> None:
        """Compatibility wrapper for visualization.plot_feasibility."""
        from .visualization import plot_feasibility

        plot_feasibility(self, path, threshold=threshold)

    def plot_paths(self, path: str, sortie_index: int = 0) -> None:
        """Compatibility wrapper for visualization.plot_paths."""
        from .visualization import plot_paths

        plot_paths(self, path, sortie_index=sortie_index)

    def plot_gantt(self, path: str) -> None:
        """Compatibility wrapper for visualization.plot_gantt."""
        from .visualization import plot_gantt

        plot_gantt(self, path)

    def plot_leg_energy(self, path: str) -> None:
        """Compatibility wrapper for visualization.plot_leg_energy."""
        from .visualization import plot_leg_energy

        plot_leg_energy(self, path)

    def _build_rows(self) -> list[_RunRow]:
        rows: list[_RunRow] = []
        planned_energies = self.solution.planned_metrics.sortie_energies

        for run in self.results:
            condition_label = self._condition_label(getattr(run, "condition", None))
            replication = int(getattr(run, "replication", 0))

            for drone_result in getattr(run, "drone_results", []):
                for sortie_result in getattr(drone_result, "sortie_results", []):
                    idx = int(sortie_result.sortie_index)
                    if idx < 0 or idx >= len(self.solution.sorties):
                        continue

                    planned_time = float(self.solution.planned_metrics.sortie_times[idx])
                    planned_energy = (
                        float(planned_energies[idx]) if planned_energies is not None else None
                    )
                    planned_distance = self._planned_sortie_distance(idx)

                    rows.append(
                        _RunRow(
                            sortie_index=idx,
                            drone_id=int(sortie_result.drone_id),
                            condition=condition_label,
                            replication=replication,
                            planned_time=planned_time,
                            actual_time=float(sortie_result.actual_time),
                            planned_energy=planned_energy,
                            actual_energy=float(sortie_result.actual_energy),
                            planned_distance=planned_distance,
                            actual_distance=float(sortie_result.actual_distance),
                            corrected_battery_at_end=float(sortie_result.corrected_battery_at_end),
                            feasible=bool(sortie_result.feasible),
                        )
                    )
        return rows

    def _build_leg_rows(self) -> list[_LegRunRow]:
        rows: list[_LegRunRow] = []
        planned_leg_times = self.solution.planned_metrics.sortie_leg_times or []

        for run in self.results:
            condition_label = self._condition_label(getattr(run, "condition", None))
            replication = int(getattr(run, "replication", 0))

            for drone_result in getattr(run, "drone_results", []):
                for sortie_result in getattr(drone_result, "sortie_results", []):
                    idx = int(sortie_result.sortie_index)
                    if idx < 0 or idx >= len(planned_leg_times):
                        continue

                    planned_by_name = {
                        self._normalized_leg_name(leg_timing.name): leg_timing.duration
                        for leg_timing in planned_leg_times[idx]
                    }
                    for leg_timing in getattr(sortie_result, "leg_timings", []) or []:
                        leg_name = self._normalized_leg_name(leg_timing.name)
                        planned_time = planned_by_name.get(leg_name)
                        if planned_time is None:
                            continue
                        rows.append(
                            _LegRunRow(
                                sortie_index=idx,
                                drone_id=int(sortie_result.drone_id),
                                leg_name=leg_name,
                                condition=condition_label,
                                replication=replication,
                                planned_time=float(planned_time),
                                actual_time=float(leg_timing.duration),
                            )
                        )

        return rows

    def _planned_sortie_distance(self, sortie_index: int) -> float:
        sortie = self.solution.sorties[sortie_index]
        launch = self._node_to_gps(self.solution.launch_node(sortie_index))
        delivery = self._node_to_gps(sortie.delivery)
        rendezvous = self._node_to_gps(sortie.rendezvous)

        leg1 = haversine_distance(launch[0], launch[1], delivery[0], delivery[1])
        leg2 = haversine_distance(delivery[0], delivery[1], rendezvous[0], rendezvous[1])
        return leg1 + leg2

    def _node_to_gps(self, node_id: int) -> tuple[float, float]:
        if node_id == 0:
            return self.solution.problem.depot
        return self.solution.problem.customers[node_id]

    @staticmethod
    def _safe_ratio(actual: float, planned: float) -> float:
        if math.isclose(planned, 0.0):
            return 0.0
        return actual / planned

    @classmethod
    def _mean_ratio(cls, pairs: list[tuple[float, float | None]]) -> float | None:
        ratios = [
            cls._safe_ratio(actual, planned)
            for actual, planned in pairs
            if planned is not None and not math.isclose(planned, 0.0)
        ]
        if not ratios:
            return None
        return mean(ratios)

    @staticmethod
    def _normalized_leg_name(leg_name: str) -> str:
        normalized = str(leg_name)
        if normalized == "rendezvous":
            return "collection"
        return normalized

    @staticmethod
    def _condition_label(condition: Any) -> str:
        if condition is None:
            return "Unknown"
        label = getattr(condition, "label", "")
        if isinstance(label, str) and label.strip():
            return label.strip()
        return str(condition)

    @staticmethod
    def _group_by_sortie_drone_condition(
        rows: list[_RunRow],
    ) -> dict[tuple[int, int, str], list[_RunRow]]:
        grouped: dict[tuple[int, int, str], list[_RunRow]] = {}
        for row in rows:
            key = (row.sortie_index, row.drone_id, row.condition)
            grouped.setdefault(key, []).append(row)
        return grouped

    @staticmethod
    def _group_leg_rows(
        rows: list[_LegRunRow],
    ) -> dict[tuple[str, str], list[_LegRunRow]]:
        grouped: dict[tuple[str, str], list[_LegRunRow]] = {}
        for row in rows:
            key = (row.condition, row.leg_name)
            grouped.setdefault(key, []).append(row)
        return grouped

    def _leg_summary_rows(self) -> list[_LegSummaryRow]:
        grouped = self._group_leg_rows(self._leg_rows)
        summary_rows: list[_LegSummaryRow] = []
        for condition, leg_name in sorted(grouped):
            group = grouped[(condition, leg_name)]
            actual_times = [row.actual_time for row in group]
            planned_mean = mean([row.planned_time for row in group])
            actual_mean = mean(actual_times)
            summary_rows.append(
                _LegSummaryRow(
                    condition=condition,
                    leg_name=leg_name,
                    sample_count=len(group),
                    planned_time_mean=planned_mean,
                    actual_time_mean=actual_mean,
                    actual_time_std=stdev(actual_times) if len(actual_times) > 1 else 0.0,
                    time_inflation=self._safe_ratio(actual_mean, planned_mean),
                )
            )
        return summary_rows

    def _paper_leg_summary_rows(self) -> list[_PaperLegSummaryRow]:
        grouped: dict[tuple[str, str], list[_LegRunRow]] = {}
        source_legs_by_group: dict[tuple[str, str], set[str]] = {}

        for row in self._leg_rows:
            paper_group = self._paper_leg_group(row.leg_name)
            if paper_group is None:
                continue
            key = (row.condition, paper_group)
            grouped.setdefault(key, []).append(row)
            source_legs_by_group.setdefault(key, set()).add(row.leg_name)

        summary_rows: list[_PaperLegSummaryRow] = []
        for condition, paper_leg_group in sorted(
            grouped,
            key=lambda key: (key[0], self._paper_leg_group_sort_key(key[1]), key[1]),
        ):
            group = grouped[(condition, paper_leg_group)]
            actual_times = [row.actual_time for row in group]
            planned_times = [row.planned_time for row in group]
            summary_rows.append(
                _PaperLegSummaryRow(
                    condition=condition,
                    paper_leg_group=paper_leg_group,
                    source_legs=tuple(sorted(source_legs_by_group[(condition, paper_leg_group)])),
                    sample_count=len(group),
                    planned_time_mean=mean(planned_times),
                    actual_time_mean=mean(actual_times),
                    actual_time_std=stdev(actual_times) if len(actual_times) > 1 else 0.0,
                    time_inflation=self._safe_ratio(mean(actual_times), mean(planned_times)),
                )
            )

        return summary_rows

    @staticmethod
    def _paper_leg_group(leg_name: str) -> str | None:
        mapping = {
            "launch": "launch_fixed",
            "launch_prep": "launch_fixed",
            "launch_takeoff": "vertical_takeoff",
            "outbound": "cruise_outbound",
            "delivery_land": "vertical_landing",
            "delivery": "service",
            "delivery_takeoff": "vertical_takeoff",
            "return": "cruise_return",
            "waiting": None,
            "collection": "recovery_fixed",
            "recovery_land": "vertical_landing",
            "recovery": "recovery_fixed",
        }
        return mapping.get(leg_name, leg_name)

    @staticmethod
    def _paper_leg_group_sort_key(group_name: str) -> int:
        order = {
            "launch_fixed": 0,
            "vertical_takeoff": 1,
            "cruise_outbound": 2,
            "vertical_landing": 3,
            "service": 4,
            "cruise_return": 5,
            "recovery_fixed": 6,
        }
        return order.get(group_name, 99)

    @staticmethod
    def _latex_escape(text: str) -> str:
        repl = {
            "\\": r"\textbackslash{}",
            "&": r"\&",
            "%": r"\%",
            "$": r"\$",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
            "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
        }
        return "".join(repl.get(ch, ch) for ch in text)

    @staticmethod
    def _ensure_parent_dir(path: str) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    @staticmethod
    def _print_table(headers: list[str], rows: list[list[str]]) -> None:
        widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(cell))

        def _fmt(r: list[str]) -> str:
            return " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(r))

        print(_fmt(headers))
        print("-+-".join("-" * w for w in widths))
        for row in rows:
            print(_fmt(row))
