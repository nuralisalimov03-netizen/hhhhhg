"""Semantic data checks for the own v2 curriculum; no model inference required."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest

from curriculum_v2 import OLD_DATA, build, write


def prompt_key(example: dict) -> str:
    """The full conditioning conversation, including history for memory tasks."""
    return "\n".join(
        message["role"] + ":" + " ".join(message["content"].casefold().split())
        for message in example["messages"][:-1]
    )


def contains_value(test: unittest.TestCase, text: str, value: object) -> None:
    if type(value) is int:
        test.assertRegex(text, rf"(?<!\d){re.escape(str(value))}(?!\d)")
    else:
        test.assertIn(str(value), text)


class CurriculumV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = build(seed=20261105, train_size=600, validation_size=120, test_size=80)

    def examples(self):
        for split in ("train", "validation", "test"):
            yield from self.dataset[split]

    def test_counts_and_complete_alternating_conversations(self) -> None:
        self.assertIsInstance(self.dataset["meta"], dict)
        for split, count in (("train", 600), ("validation", 120), ("test", 80)):
            self.assertEqual(len(self.dataset[split]), count)
            for example in self.dataset[split]:
                with self.subTest(split=split, id=example.get("id")):
                    self.assertEqual(example["split"], split)
                    for key in ("id", "category", "family"):
                        self.assertIsInstance(example[key], str)
                        self.assertTrue(example[key].strip())
                    messages = example["messages"]
                    self.assertIn(len(messages), (2, 4))
                    self.assertEqual([message["role"] for message in messages], ["user", "assistant"] * (len(messages) // 2))
                    for message in messages:
                        self.assertIsInstance(message["content"], str)
                        self.assertTrue(message["content"].strip())
                        self.assertNotIn("<|im_start|>", message["content"])
                        self.assertNotIn("<|im_end|>", message["content"])
                    self.assertIn(example["task"]["kind"], {"arithmetic", "linear", "copy", "code", "authored", "multiturn"})
                    self.assertIsInstance(example["task"]["split_key"], str)
                    self.assertTrue(example["task"]["split_key"])

    def test_reproducible_examples_order_and_metadata(self) -> None:
        repeated = build(seed=20261105, train_size=600, validation_size=120, test_size=80)
        self.assertEqual(repeated, self.dataset)
        changed = build(seed=20261106, train_size=600, validation_size=120, test_size=80)
        self.assertNotEqual(changed["train"], self.dataset["train"])

    def test_ids_and_full_prompts_are_globally_unique(self) -> None:
        examples = list(self.examples())
        ids = [example["id"] for example in examples]
        prompts = [prompt_key(example) for example in examples]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(prompts), len(set(prompts)))

    def test_reservation_keys_and_wording_families_do_not_cross_splits(self) -> None:
        for field in ("family", "split_key"):
            keys = {
                split: {
                    example["family"] if field == "family" else example["task"]["split_key"]
                    for example in self.dataset[split]
                }
                for split in ("train", "validation", "test")
            }
            for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
                with self.subTest(field=field, splits=(left, right)):
                    self.assertFalse(keys[left] & keys[right])

    def test_operand_pairs_never_cross_splits_even_when_reversed(self) -> None:
        pairs = {}
        for split in ("train", "validation", "test"):
            pairs[split] = {
                tuple(sorted(example["task"]["operands"]))
                for example in self.dataset[split]
                if example["task"]["kind"] == "arithmetic"
            }
            self.assertTrue(pairs[split])
        for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
            self.assertFalse(pairs[left] & pairs[right])

    def test_original_v1_heldout_prompts_and_numeric_tasks_stay_held_out(self) -> None:
        original = [json.loads(line) for line in (OLD_DATA / 'validation.jsonl').read_text().splitlines()]
        prompts = {row['messages'][0]['content'] for row in original}
        pairs = {tuple(sorted(row['task']['operands'])) for row in original
                 if row['task']['kind'] == 'arithmetic'}
        triples = {tuple(row['task']['operands']) for row in original
                   if row['task']['kind'] == 'linear'}
        for row in self.dataset['train']:
            for message in row['messages']:
                if message['role'] == 'user':
                    self.assertNotIn(message['content'], prompts)
            if row['task']['kind'] == 'arithmetic':
                self.assertNotIn(tuple(sorted(row['task']['operands'])), pairs)
            elif row['task']['kind'] == 'linear':
                self.assertNotIn(tuple(row['task']['operands']), triples)

    def test_arithmetic_metadata_and_answer_are_correct(self) -> None:
        operations = set()
        for example in self.examples():
            task = example["task"]
            if task["kind"] != "arithmetic":
                continue
            with self.subTest(id=example["id"]):
                a, b = task["operands"]
                result = task["result"]
                self.assertTrue(all(type(value) is int for value in (a, b, result)))
                operation = task.get("operation", task.get("op"))
                normalized = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}.get(operation, operation)
                operations.add(normalized)
                if normalized == "add":
                    self.assertEqual(a + b, result)
                elif normalized == "subtract":
                    self.assertEqual(a - b, result)
                elif normalized == "multiply":
                    self.assertEqual(a * b, result)
                elif normalized == "divide":
                    self.assertNotEqual(b, 0)
                    self.assertEqual(b * result, a)
                else:
                    self.fail(f"Unknown arithmetic operation: {operation!r}")
                contains_value(self, example["messages"][-1]["content"], result)
        self.assertEqual(operations, {"add", "subtract", "multiply", "divide"})

    def test_linear_solutions_satisfy_the_equation(self) -> None:
        count = 0
        for example in self.examples():
            task = example["task"]
            if task["kind"] != "linear":
                continue
            count += 1
            coefficient, constant, solution = task["operands"]
            with self.subTest(id=example["id"]):
                self.assertNotEqual(coefficient, 0)
                self.assertEqual(coefficient * solution + constant, task["rhs"])
                self.assertEqual(solution, task["result"])
                contains_value(self, example["messages"][-1]["content"], solution)
        self.assertGreater(count, 0)

    def test_code_slots_are_fully_stated_and_preserved(self) -> None:
        topics = set()
        expected = {
            "counter": {"initial", "step"},
            "card": {"title", "color", "padding"},
            "function": {"name", "increment", "input"},
        }
        for example in self.examples():
            task = example["task"]
            if task["kind"] != "code":
                continue
            with self.subTest(id=example["id"]):
                topic = task["topic"]
                topics.add(topic)
                self.assertIn(topic, expected)
                self.assertEqual(set(task["slots"]), expected[topic])
                question = "\n".join(message["content"] for message in example["messages"] if message["role"] == "user")
                answer = example["messages"][-1]["content"]
                for slot, value in task["slots"].items():
                    with self.subTest(slot=slot):
                        contains_value(self, question, value)
                        contains_value(self, answer, value)
        self.assertEqual(topics, set(expected))

    def test_copy_values_are_in_the_question_and_answer(self) -> None:
        count = 0
        for example in self.examples():
            task = example["task"]
            if task["kind"] != "copy":
                continue
            count += 1
            with self.subTest(id=example["id"]):
                self.assertTrue(task["field"])
                contains_value(self, example["messages"][0]["content"], task["value"])
                contains_value(self, example["messages"][-1]["content"], task["value"])
        self.assertGreater(count, 0)

    def test_multiturn_answers_use_the_supplied_history(self) -> None:
        count = 0
        for example in self.examples():
            task = example["task"]
            if task["kind"] != "multiturn":
                continue
            count += 1
            with self.subTest(id=example["id"]):
                self.assertEqual(len(example["messages"]), 4)
                history = "\n".join(message["content"] for message in example["messages"][:-1])
                contains_value(self, history, task["target"])
                contains_value(self, example["messages"][-1]["content"], task["target"])
        self.assertGreater(count, 0)

    def test_jsonl_roundtrip_and_integrity_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "curriculum"
            meta = write(output, seed=20261105, train_size=600, validation_size=120, test_size=80)
            self.assertEqual(json.loads((output / "meta.json").read_text(encoding="utf-8")), meta)
            for split in ("train", "validation", "test"):
                filename = split + ".jsonl"
                raw = (output / filename).read_bytes()
                self.assertTrue(raw.endswith(b"\n"))
                actual = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
                self.assertEqual(actual, self.dataset[split])
                recorded = meta["files"][filename]
                self.assertEqual(recorded["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual(recorded["bytes"], len(raw))
                self.assertEqual(recorded["examples"], len(actual))

    def test_final_benchmark_cannot_be_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            write(output, seed=20261105, train_size=60, validation_size=30, test_size=40)
            test = output / 'test.jsonl'
            before = test.read_bytes()
            timestamp = test.stat().st_mtime_ns
            write(output, seed=20261105, train_size=60, validation_size=30, test_size=40)
            self.assertEqual(test.stat().st_mtime_ns, timestamp)
            with self.assertRaises(ValueError):
                write(output, seed=20261106, train_size=60, validation_size=30, test_size=40)
            self.assertEqual(test.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
