import json

from demo_mission.experiment import main


def test_run_experiment_dry_run_outputs_liftoff_result(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LIFTOFF_DRY_RUN", "1")
    monkeypatch.setenv("LIFTOFF_SCENARIO", "crosswind")
    monkeypatch.setenv(
        "LIFTOFF_PARAMS_JSON",
        json.dumps(
            {
                "flight_plan": {
                    "home": [38.898, -77.036],
                    "waypoints": [[38.899, -77.035]],
                    "altitude_m": 20,
                    "speed_m_s": 8,
                }
            }
        ),
    )
    monkeypatch.setenv("LIFTOFF_OUTPUT_DIR", str(tmp_path))

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["scenario"] == "crosswind"
    assert payload["status"] == "passed"
    assert payload["pass_criteria"]["mission_completed"] is True
    assert payload["runs"][0]["sorties"][0]["planned_time_s"] > 0
    assert (tmp_path / "result.json").exists()
