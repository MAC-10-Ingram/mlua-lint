import io
import json
import unittest
from argparse import Namespace
from contextlib import redirect_stdout

from mlua_lint.cli import parse_diagnostic_severities, run
from mlua_lint.errors import ValidationError


class ParseDiagnosticSeveritiesTests(unittest.TestCase):
    def test_none_when_empty(self) -> None:
        self.assertIsNone(parse_diagnostic_severities([]))

    def test_parse_repeatable_and_comma_separated(self) -> None:
        values = ["warning,error", "information", "warning"]
        self.assertEqual(parse_diagnostic_severities(values), {"warning", "error", "information"})

    def test_reject_invalid_values(self) -> None:
        with self.assertRaises(ValidationError):
            parse_diagnostic_severities(["hint"])


class CliCommandShapeTests(unittest.TestCase):
    def test_diagnostic_subcommand_is_rejected(self) -> None:
        args = Namespace(
            files=["diagnostic", "sample.mlua"],
            ls_path="",
            root="",
            format="json",
            timeout=5,
            severity=[],
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = run(args)

        self.assertEqual(exit_code, 1)
        envelope = json.loads(stdout.getvalue())
        self.assertEqual(envelope["error"]["code"], "invalid_argument")
        self.assertIn("removed", envelope["error"]["message"])


if __name__ == "__main__":
    unittest.main()
