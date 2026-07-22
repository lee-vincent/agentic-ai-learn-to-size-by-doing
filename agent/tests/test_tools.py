"""Unit tests for the calculator + kb_lookup tools. No network involved."""
import json
import unittest

from agent.tools import calculator, execute_tool_call, kb_lookup


class TestCalculator(unittest.TestCase):
    def test_basic_arithmetic(self):
        self.assertEqual(calculator({"expression": "12 * 7"}), {"expression": "12 * 7", "result": 84})

    def test_parentheses_and_precedence(self):
        result = calculator({"expression": "2 + 3 * (4 - 1)"})
        self.assertEqual(result["result"], 11)

    def test_functions_and_constants(self):
        result = calculator({"expression": "round(sqrt(16), 2)"})
        self.assertEqual(result["result"], 4.0)

    def test_missing_expression(self):
        result = calculator({})
        self.assertIn("error", result)

    def test_empty_expression(self):
        result = calculator({"expression": "   "})
        self.assertIn("error", result)

    def test_division_by_zero_is_reported_not_raised(self):
        result = calculator({"expression": "1 / 0"})
        self.assertIn("error", result)

    def test_rejects_arbitrary_code_execution(self):
        # Not on any whitelist -> CalculatorError, surfaced as a tool error, never executed.
        result = calculator({"expression": "__import__('os').system('echo pwned')"})
        self.assertIn("error", result)

    def test_rejects_attribute_access(self):
        result = calculator({"expression": "(1).__class__"})
        self.assertIn("error", result)

    def test_rejects_unknown_name(self):
        result = calculator({"expression": "not_a_real_constant + 1"})
        self.assertIn("error", result)


class TestKbLookup(unittest.TestCase):
    def test_known_query_returns_results(self):
        result = kb_lookup({"query": "L40S VRAM"})
        self.assertIn("results", result)
        self.assertGreater(len(result["results"]), 0)
        self.assertTrue(any("44.7" in r["text"] for r in result["results"]))

    def test_unknown_query_returns_empty_with_note(self):
        result = kb_lookup({"query": "xyzzy nonexistent gibberish term"})
        self.assertEqual(result["results"], [])
        self.assertIn("note", result)

    def test_missing_query(self):
        result = kb_lookup({})
        self.assertIn("error", result)

    def test_top_k_is_respected_and_clamped(self):
        result = kb_lookup({"query": "vllm metric", "top_k": 1})
        self.assertLessEqual(len(result["results"]), 1)
        result_clamped = kb_lookup({"query": "vllm metric", "top_k": 999})
        self.assertLessEqual(len(result_clamped["results"]), 10)


class TestExecuteToolCall(unittest.TestCase):
    def test_dispatches_to_calculator(self):
        raw = execute_tool_call("calculator", json.dumps({"expression": "2 + 2"}))
        parsed = json.loads(raw)
        self.assertEqual(parsed["result"], 4)

    def test_unknown_tool_name(self):
        raw = execute_tool_call("not_a_tool", "{}")
        parsed = json.loads(raw)
        self.assertIn("error", parsed)

    def test_invalid_arguments_json(self):
        raw = execute_tool_call("calculator", "{not valid json")
        parsed = json.loads(raw)
        self.assertIn("error", parsed)

    def test_arguments_must_be_object(self):
        raw = execute_tool_call("calculator", "[1, 2, 3]")
        parsed = json.loads(raw)
        self.assertIn("error", parsed)

    def test_empty_arguments_string_defaults_to_empty_object(self):
        raw = execute_tool_call("calculator", "")
        parsed = json.loads(raw)
        self.assertIn("error", parsed)  # empty object -> missing 'expression'


if __name__ == "__main__":
    unittest.main()
