import unittest

from orchestrator.execution_agent import ExecutionAgent


class ExecutionAgentSpectrumTest(unittest.TestCase):
    def setUp(self):
        self.agent = ExecutionAgent.__new__(ExecutionAgent)
        self.config = {
            "channel_pattern": "10100110",
            "channel_slots": 8,
            "spacing_hz": 50e9,
            "f_min_hz": 194e12,
            "baud_rate": 32e9,
            "roll_off": 0.15,
            "tx_osnr_db": 40.0,
            "launch_power": -5.5,
        }

    def test_only_active_slots_are_emitted_and_keep_slot_frequencies(self):
        payload = self.agent._build_spectrum_payload(self.config)
        channels = payload["spectrum"]

        self.assertEqual(len(channels), 4)
        self.assertEqual(
            [channel["label"] for channel in channels],
            ["slot-1", "slot-3", "slot-6", "slot-7"],
        )
        self.assertEqual(
            [channel["f_min"] for channel in channels],
            [194e12, 194.1e12, 194.25e12, 194.3e12],
        )
        self.assertTrue(all(channel["tx_power_dbm"] == -5.5 for channel in channels))

    def test_all_off_pattern_produces_empty_spectrum(self):
        self.config["channel_pattern"] = "00000000"
        self.assertEqual(self.agent._build_spectrum_payload(self.config), {"spectrum": []})

    def test_invalid_pattern_state_is_rejected(self):
        self.config["channel_pattern"] = "10x00110"
        with self.assertRaisesRegex(ValueError, "only '0' and '1'"):
            self.agent._build_spectrum_payload(self.config)


if __name__ == "__main__":
    unittest.main()
