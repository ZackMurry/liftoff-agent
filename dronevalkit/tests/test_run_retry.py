"""Tests for per-run retry behavior in dronevalkit.run()."""

from __future__ import annotations

import asyncio

import dronevalkit as dvk
from dronevalkit.config import WindCondition
from dronevalkit.exceptions import MissionAbortedError


def test_run_single_with_retry_retries_mission_abort_until_success(monkeypatch):
    attempts: list[int] = []

    async def _fake_run_single(config, condition, rep, output_dir, base_instance):
        attempts.append(1)
        if len(attempts) < 3:
            raise MissionAbortedError("battery depleted")
        return {"status": "ok"}

    monkeypatch.setattr("dronevalkit._run_single", _fake_run_single)

    result = asyncio.run(
        dvk._run_single_with_retry(
            config=object(),
            condition=WindCondition.calm(),
            rep=0,
            output_dir="unused",
            base_instance=0,
            max_retries=2,
        )
    )

    assert result == {"status": "ok"}
    assert len(attempts) == 3


def test_run_single_with_retry_abandons_after_retry_budget(monkeypatch):
    attempts: list[int] = []

    async def _fake_run_single(config, condition, rep, output_dir, base_instance):
        attempts.append(1)
        raise MissionAbortedError("battery depleted")

    monkeypatch.setattr("dronevalkit._run_single", _fake_run_single)

    result = asyncio.run(
        dvk._run_single_with_retry(
            config=object(),
            condition=WindCondition.calm(),
            rep=0,
            output_dir="unused",
            base_instance=0,
            max_retries=2,
        )
    )

    assert result is None
    assert len(attempts) == 3
