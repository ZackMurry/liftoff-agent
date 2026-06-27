from demo_mission.flight_plan import parse_flight_plan


def test_parse_explicit_flight_plan():
    plan = parse_flight_plan(
        {
            "flight_plan": {
                "home": [38.898, -77.036],
                "altitude_m": 25,
                "speed_m_s": 9,
                "waypoints": [[38.899, -77.035], [38.9, -77.034]],
                "acceptance_radius_m": 4,
            }
        }
    )

    assert plan.home.lat == 38.898
    assert len(plan.waypoints) == 2
    assert plan.altitude_m == 25
    assert plan.speed_m_s == 9
    assert plan.acceptance_radius_m == 4
    assert plan.planned_distance_m > 0
    assert plan.planned_time_s > 0


def test_parse_flat_liftoff_params():
    plan = parse_flight_plan(
        {
            "depot": [38.898, -77.036],
            "altitude": 30,
            "drone_speed": 7,
            "waypoints": [[38.899, -77.035]],
        }
    )

    assert plan.home.lon == -77.036
    assert plan.altitude_m == 30
    assert plan.speed_m_s == 7
    assert len(plan.waypoints) == 1


def test_parse_emergency_stop_alias_uses_safe_defaults():
    plan = parse_flight_plan({"scenario": "emergency_stop", "speed_m_s": 9})

    assert plan.speed_m_s == 9
    assert plan.acceptance_radius_m == 4
    assert len(plan.waypoints) >= 1
