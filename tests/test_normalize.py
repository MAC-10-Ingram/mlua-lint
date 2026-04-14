import unittest

from mlua_lint.normalize import normalize_annotation_result, normalize_location_result


class NormalizeTests(unittest.TestCase):
    def test_normalize_location_result(self) -> None:
        raw = {
            "uri": "file:///tmp/test.mlua",
            "range": {"start": {"line": 1, "character": 2}, "end": {"line": 1, "character": 6}},
        }
        locations = normalize_location_result(raw)
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0]["uri"], "file:///tmp/test.mlua")
        self.assertEqual(locations[0]["range"]["start"]["line"], 1)
        self.assertEqual(locations[0]["range"]["start"]["character"], 2)

    def test_normalize_annotation_result(self) -> None:
        raw = {"data": [0, 1, 3, 0, 1, 1, 2, 4, 1, 0]}
        legend = {"tokenTypes": ["keyword", "function"], "tokenModifiers": ["declaration"]}
        result = normalize_annotation_result(raw, legend)
        self.assertEqual(len(result["tokens"]), 2)
        self.assertEqual(result["tokens"][0]["tokenType"], "keyword")
        self.assertEqual(result["tokens"][1]["line"], 1)
        self.assertEqual(result["tokens"][1]["startCharacter"], 2)


if __name__ == "__main__":
    unittest.main()
