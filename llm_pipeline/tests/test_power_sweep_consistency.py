import unittest

from orchestrator.reflection_agent import ReflectionAgent
from orchestrator.scenario_expander_agent import ScenarioExpanderAgent


class ReflectionPowerSweepTest(unittest.TestCase):
    def test_single_observed_power_does_not_create_family_override(self):
        analysis = {
            "unique_launch_powers_dbm": [-5.5],
            "best_per_family": {
                "reading|voyager|QPSK|32|100|11100000": {
                    "scenario": {"power": -5.5},
                },
            },
        }

        derived = ReflectionAgent()._derive_family_power_overrides(analysis, {})

        self.assertEqual(derived, {})


class _StubScenarioExpander(ScenarioExpanderAgent):
    def __init__(self, blueprint):
        self.blueprint = blueprint
        self._equipment_cache = None
        self._transceiver_modes_cache = None

    def _load_transceiver_modes(self):
        return {
            "voyager": [
                {
                    "transmitter_type": "voyager",
                    "modulation": "QPSK",
                    "baud_rate_gbaud": 32.0,
                    "bit_rate_gbps": 100.0,
                    "min_spacing_ghz": 37.5,
                    "tx_osnr_db": 40.0,
                }
            ]
        }

    def _ask_llm_for_blueprint(self, task, path_bundle, reflection):
        return self.blueprint


class ScenarioPowerSweepConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.sweep = [-6.5, -6.0, -5.5]
        self.task = {
            "network": "NDFF",
            "source": "bristol",
            "preferred_engine": "gnpy",
            "power_sweep_dbm": self.sweep,
            "modulations": ["QPSK"],
            "transmitter_inventory": {"voyager": 1},
            "channel_slots": 1,
            "channel_activation_mode": "fixed_pattern",
            "fixed_channel_pattern": "1",
            "max_channel_patterns": 1,
            "max_dataset_points": 10,
            "random_seed": 1,
        }
        self.path_bundle = {
            "network": "NDFF",
            "source": "bristol",
            "paths": [
                {
                    "network": "NDFF",
                    "source": "bristol",
                    "destination": "reading",
                    "nodes": ["bristol", "reading"],
                    "spans": [],
                    "total_distance_km": 1.0,
                }
            ],
        }
        self.blueprint = {
            "paths": [
                {
                    "destination": "reading",
                    "transmitter_types": ["voyager"],
                    "modulations": ["QPSK"],
                    "channel_slots": 1,
                    "channel_activation_mode": "fixed_pattern",
                    "fixed_channel_pattern": "1",
                    "max_channel_patterns": 1,
                    "preferred_engine": "gnpy",
                    "power_sweep_dbm": list(self.sweep),
                }
            ]
        }

    def test_planner_and_expander_mismatch_fails_before_generation(self):
        self.blueprint["paths"][0]["power_sweep_dbm"] = [-5.5, -5.0, -4.5]
        agent = _StubScenarioExpander(self.blueprint)

        with self.assertRaisesRegex(ValueError, "Planner vs Scenario Expander"):
            agent.expand(self.task, self.path_bundle, reflection=None, iteration=2)

    def test_reflection_override_mismatch_fails_instead_of_silently_winning(self):
        mode = {
            "transmitter_type": "voyager",
            "modulation": "QPSK",
            "baud_rate_gbaud": 32.0,
            "bit_rate_gbps": 100.0,
        }
        agent = _StubScenarioExpander(self.blueprint)
        family_key = agent._scenario_family_key(
            "reading", mode, "1"
        )
        reflection = {
            "planner_updates": {"power_sweep_dbm": list(self.sweep)},
            "scenario_power_overrides": {family_key: [-5.5, -5.0, -4.5]},
        }
        with self.assertRaisesRegex(ValueError, "Reflection override vs Scenario Expander"):
            agent.expand(self.task, self.path_bundle, reflection=reflection, iteration=2)

    def test_matching_sweep_is_emitted_exactly(self):
        mode = {
            "transmitter_type": "voyager",
            "modulation": "QPSK",
            "baud_rate_gbaud": 32.0,
            "bit_rate_gbps": 100.0,
        }
        agent = _StubScenarioExpander(self.blueprint)
        family_key = agent._scenario_family_key(
            "reading", mode, "1"
        )
        reflection = {
            "planner_updates": {"power_sweep_dbm": list(self.sweep)},
            "scenario_power_overrides": {family_key: list(self.sweep)},
        }
        scenarios = agent.expand(self.task, self.path_bundle, reflection=reflection, iteration=2)

        self.assertEqual(
            sorted({scenario["power"] for scenario in scenarios}),
            self.sweep,
        )


if __name__ == "__main__":
    unittest.main()
