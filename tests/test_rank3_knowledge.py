"""C1/D1 단위 테스트: conflict / skill_plugins."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import core.conflict as conflict
import core.skill_plugins as skill_plugins


class TestConflict(unittest.TestCase):
    def test_no_conflict(self):
        triples = [
            {"subject": "A", "predicate": "는", "object": "B", "layer": "semantic"},
            {"subject": "C", "predicate": "는", "object": "D", "layer": "semantic"},
        ]
        self.assertEqual(conflict.detect_conflicts(triples), [])

    def test_same_spo_conflict(self):
        triples = [
            {"subject": "A", "predicate": "는", "object": "B", "layer": "semantic"},
            {"subject": "A", "predicate": "는", "object": "C", "layer": "semantic"},
        ]
        result = conflict.detect_conflicts(triples)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "same_spo")
        self.assertEqual(result[0]["objects"], ["B", "C"])

    def test_negation_conflict(self):
        triples = [
            {"subject": "A", "predicate": "는", "object": "B", "layer": "semantic"},
            {"subject": "A", "predicate": "는", "object": "B가 아니다", "layer": "semantic"},
        ]
        result = conflict.detect_conflicts(triples)
        self.assertEqual(result[0]["type"], "negation")

    def test_duplicate_object_no_conflict(self):
        triples = [
            {"subject": "A", "predicate": "는", "object": "B", "layer": "semantic"},
            {"subject": "A", "predicate": "는", "object": "B", "layer": "semantic"},
        ]
        self.assertEqual(conflict.detect_conflicts(triples), [])

    def test_report_empty(self):
        self.assertEqual(conflict.conflict_report([]), "")

    def test_report_format(self):
        text = conflict.conflict_report([
            {"type": "same_spo", "subject": "A", "predicate": "는", "objects": ["B", "C"]}
        ])
        self.assertIn("지식 충돌 1건", text)
        self.assertIn("A", text)


class TestSkillPlugins(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = skill_plugins.SKILLS_FILE
        skill_plugins.SKILLS_FILE = Path(self._tmp.name) / "custom_skills.json"

    def tearDown(self):
        skill_plugins.SKILLS_FILE = self._old
        self._tmp.cleanup()

    def test_add_and_load(self):
        skill_plugins.add_custom_skill("인사", "인사말", "안녕하세요, {args}님!")
        skills = skill_plugins.load_custom_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["name"], "인사")

    def test_add_updates_existing(self):
        skill_plugins.add_custom_skill("인사", "v1", "안녕")
        skill_plugins.add_custom_skill("인사", "v2", "반가워요")
        skills = skill_plugins.load_custom_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["template"], "반가워요")

    def test_render_and_execute(self):
        skill_plugins.add_custom_skill("인사", "인사말", "안녕하세요, {args}님!")
        self.assertEqual(skill_plugins.execute_custom_skill("인사", "철수"), "안녕하세요, 철수님!")
        self.assertIsNone(skill_plugins.execute_custom_skill("없는스킬"))

    def test_remove(self):
        skill_plugins.add_custom_skill("인사", "d", "t")
        self.assertTrue(skill_plugins.remove_custom_skill("인사"))
        self.assertFalse(skill_plugins.remove_custom_skill("인사"))

    def test_register_all(self):
        skill_plugins.add_custom_skill("인사", "인사말", "안녕, {args}!")
        count = skill_plugins.register_all()
        self.assertEqual(count, 1)
        from core import bot_skills
        self.assertIn("철수", bot_skills.execute_skill("인사", "철수"))

    def test_parse_define_command(self):
        parsed = skill_plugins.parse_skill_define_command("스킬 만들어줘 이름:인사 설명:인사말 답변:안녕 {args}!")
        self.assertEqual(parsed["name"], "인사")
        self.assertEqual(parsed["template"], "안녕 {args}!")
        self.assertIsNone(skill_plugins.parse_skill_define_command("오늘 날씨"))


if __name__ == "__main__":
    unittest.main()
