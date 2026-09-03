import unittest

from core.shacl_validator import validate_batch
from core.triple_extractor import extract_triples_from_turn


class OntologyPipelineTests(unittest.TestCase):
    def test_valid_triple_passes_and_invalid_predicate_is_rejected(self):
        results = validate_batch([
            {"subject": "WeAid", "predicate": "is", "object": "assistant", "layer": "semantic"},
            {"subject": "WeAid", "predicate": "unknown", "object": "assistant", "layer": "semantic"},
        ])
        self.assertTrue(results[0]["valid"])
        self.assertFalse(results[1]["valid"])

    def test_korean_identity_sentence_produces_a_semantic_proposal(self):
        triples = extract_triples_from_turn("WeAid는 음성 비서입니다.", "")
        self.assertIn(
            {"subject": "WeAid", "predicate": "is", "object": "음성 비서입니다", "layer": "semantic"},
            triples,
        )


if __name__ == "__main__":
    unittest.main()