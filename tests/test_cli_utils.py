import unittest

from mlua_lint.cli import parse_diagnostic_severities, parse_position
from mlua_lint.errors import ValidationError


class ParsePositionTests(unittest.TestCase):
    def test_parse_position(self) -> None:
        self.assertEqual(parse_position("12:34"), {"line": 12, "character": 34})

    def test_parse_position_rejects_invalid_input(self) -> None:
        for value in ("", "12", "a:1", "1:b", "-1:2", "1:-3"):
            with self.assertRaises(ValidationError):
                parse_position(value)


class ParseDiagnosticSeveritiesTests(unittest.TestCase):
    def test_none_when_empty(self) -> None:
        self.assertIsNone(parse_diagnostic_severities([]))

    def test_parse_repeatable_and_comma_separated(self) -> None:
        values = ["warning,error", "information", "warning"]
        self.assertEqual(parse_diagnostic_severities(values), {"warning", "error", "information"})

    def test_reject_invalid_values(self) -> None:
        with self.assertRaises(ValidationError):
            parse_diagnostic_severities(["hint"])


if __name__ == "__main__":
    unittest.main()
