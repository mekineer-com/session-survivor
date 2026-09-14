import unittest

from grok_usage_stats import render


class GrokUsageStatsTest(unittest.TestCase):
    def test_render_cost_context_and_turns(self):
        text = render({
            "session_id": "synthetic", "first": "2026-01-01T00:00:00Z",
            "last": "2026-01-02T00:00:00Z", "turns": 2, "calls": 4,
            "input": 1_000_000, "output": 2_000, "cache_percent": 80,
            "cost": 1.25, "incomplete": True, "context_window": 500_000,
            "chat_bytes": 1_250_000, "native_compactions": 2,
            "current_input": 250_000,
            "days": {"2026-01-02": {"turns": 2, "calls": 4,
                                      "input": 1_000_000, "cost": 12_500_000_000}},
            "worst": [{"turnNumber": 7, "modelCalls": 3, "inputTokens": 750_000,
                       "costUsdTicks": 10_000_000_000}],
            "prompts": {7: "Synthetic prompt"},
        })
        self.assertIn("2.0 calls per turn", text)
        self.assertIn("Current prompt estimate: 250k / 500k tokens (50%)", text)
        self.assertIn("$1.25 (usage marked incomplete)", text)
        self.assertIn("Chat/native compactions: 1.25 MB / 2", text)
        self.assertIn("7: 3 calls", text)


if __name__ == "__main__":
    unittest.main()
