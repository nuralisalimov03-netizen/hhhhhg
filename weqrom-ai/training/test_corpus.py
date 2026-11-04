"""Semantic checks for the locally authored, deterministic training corpus.

These tests exercise generator invariants and arithmetic, not model quality.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest

from corpus import build_corpus, format_chatml, operand_key, write_corpus


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def prompt_key(example: dict) -> str:
    return " ".join(example["messages"][0]["content"].casefold().split())


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.train, cls.validation, cls.meta = build_corpus(
            seed=20261104, train_size=600, validation_size=100
        )

    def test_requested_counts_and_message_structure(self) -> None:
        self.assertEqual(len(self.train), 600)
        self.assertEqual(len(self.validation), 100)
        for split, examples in (("train", self.train), ("validation", self.validation)):
            for example in examples:
                with self.subTest(split=split, id=example.get("id")):
                    self.assertEqual(example["split"], split)
                    self.assertIsInstance(example["id"], str)
                    self.assertTrue(example["id"])
                    self.assertIsInstance(example["category"], str)
                    self.assertTrue(example["category"])
                    self.assertIsInstance(example["family"], str)
                    self.assertTrue(example["family"])
                    messages = example["messages"]
                    self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
                    for message in messages:
                        self.assertIsInstance(message["content"], str)
                        self.assertTrue(message["content"].strip())
                        self.assertNotIn("<|im_start|>", message["content"])
                        self.assertNotIn("<|im_end|>", message["content"])
                    self.assertIsInstance(example["task"], dict)
                    self.assertIn(example["task"]["kind"], {"arithmetic", "linear", "authored", "code"})

    def test_seed_reproduces_contents_order_and_metadata(self) -> None:
        repeated = build_corpus(seed=20261104, train_size=600, validation_size=100)
        self.assertEqual((self.train, self.validation, self.meta), repeated)
        original_hash = hashlib.sha256(canonical_json([self.train, self.validation])).hexdigest()
        changed_train, changed_validation, _ = build_corpus(
            seed=20261105, train_size=600, validation_size=100
        )
        changed_hash = hashlib.sha256(canonical_json([changed_train, changed_validation])).hexdigest()
        self.assertNotEqual(original_hash, changed_hash)

    def test_ids_and_prompts_are_unique_across_both_splits(self) -> None:
        examples = self.train + self.validation
        ids = [example["id"] for example in examples]
        prompts = [prompt_key(example) for example in examples]
        self.assertEqual(len(ids), len(set(ids)), "An example ID is repeated")
        self.assertEqual(len(prompts), len(set(prompts)), "A prompt is repeated after whitespace/case normalization")

    def test_validation_wording_families_are_entirely_held_out(self) -> None:
        train_families = {example["family"] for example in self.train}
        validation_families = {example["family"] for example in self.validation}
        self.assertTrue(train_families)
        self.assertTrue(validation_families)
        self.assertFalse(train_families & validation_families)

    def test_algorithmic_wording_is_held_out_after_removing_numbers(self) -> None:
        def skeletons(examples: list[dict]) -> set[tuple[str, str]]:
            return {
                (example["task"]["kind"], re.sub(r"[+\-−]?\d+", "#", prompt_key(example)))
                for example in examples
                if example["task"]["kind"] in {"arithmetic", "linear"}
            }

        self.assertFalse(
            skeletons(self.train) & skeletons(self.validation),
            "Held-out family names must also correspond to different prompt wording",
        )

    def test_arithmetic_operand_pairs_do_not_leak_through_another_operation(self) -> None:
        def pairs(examples: list[dict]) -> set[tuple[int, int]]:
            return {
                tuple(sorted(example["task"]["operands"]))
                for example in examples
                if example["task"]["kind"] == "arithmetic"
            }

        train_pairs = pairs(self.train)
        validation_pairs = pairs(self.validation)
        self.assertTrue(train_pairs)
        self.assertTrue(validation_pairs)
        self.assertFalse(train_pairs & validation_pairs)

    def test_algorithmic_operand_keys_are_disjoint(self) -> None:
        train_keys = {operand_key(example) for example in self.train} - {None}
        validation_keys = {operand_key(example) for example in self.validation} - {None}
        self.assertTrue(train_keys)
        self.assertTrue(validation_keys)
        self.assertFalse(train_keys & validation_keys)
        for example in self.train + self.validation:
            if example["task"]["kind"] == "authored":
                self.assertIsNone(operand_key(example))

    def test_arithmetic_results_are_mathematically_correct(self) -> None:
        operations_seen = set()
        for example in self.train + self.validation:
            task = example["task"]
            if task["kind"] != "arithmetic":
                continue
            with self.subTest(id=example["id"]):
                a, b = task["operands"]
                result = task["result"]
                self.assertTrue(all(type(value) is int for value in (a, b, result)))
                operation = task["operation"]
                operations_seen.add(operation)
                if operation == "add":
                    self.assertEqual(a + b, result)
                elif operation == "subtract":
                    self.assertEqual(a - b, result)
                elif operation == "multiply":
                    self.assertEqual(a * b, result)
                elif operation == "divide":
                    self.assertNotEqual(b, 0)
                    self.assertEqual(a, b * result)
                else:
                    self.fail(f"Unknown operation: {operation!r}")
                answer = example["messages"][1]["content"]
                self.assertRegex(answer, rf"(?<!\d){re.escape(str(result))}(?!\d)")
        self.assertEqual(operations_seen, {"add", "subtract", "multiply", "divide"})

    def test_linear_equations_have_the_claimed_solution(self) -> None:
        count = 0
        for example in self.train + self.validation:
            task = example["task"]
            if task["kind"] != "linear":
                continue
            count += 1
            with self.subTest(id=example["id"]):
                coefficient, constant, solution = task["operands"]
                self.assertNotEqual(coefficient, 0)
                self.assertEqual(coefficient * solution + constant, task["rhs"])
                self.assertEqual(solution, task["result"])
                self.assertRegex(example["messages"][1]["content"], rf"(?<!\d){re.escape(str(solution))}(?!\d)")
        self.assertGreater(count, 0)

    def test_numeric_derivations_in_answer_text_are_true(self) -> None:
        equations_checked = 0
        pattern = re.compile(r"(?<![\d.])(-?\d+)\s*([+×:/-])\s*(-?\d+)\s*=\s*(-?\d+)(?!\d)")
        for example in self.train + self.validation:
            if example["task"]["kind"] != "arithmetic":
                continue
            for match in pattern.finditer(example["messages"][1]["content"]):
                a, operation, b, result = match.groups()
                a, b, result = int(a), int(b), int(result)
                equations_checked += 1
                with self.subTest(id=example["id"], equation=match.group()):
                    if operation == "+":
                        self.assertEqual(a + b, result)
                    elif operation == "-":
                        self.assertEqual(a - b, result)
                    elif operation == "×":
                        self.assertEqual(a * b, result)
                    else:
                        self.assertNotEqual(b, 0)
                        self.assertEqual(a, result * b)
        self.assertGreater(equations_checked, 300)

    def test_chatml_preserves_dialogue_and_has_no_system_message(self) -> None:
        for example in (self.train[0], self.validation[0]):
            user, assistant = example["messages"]
            expected = (
                f"<|im_start|>user\n{user['content']}<|im_end|>\n"
                f"<|im_start|>assistant\n{assistant['content']}<|im_end|>\n"
            )
            self.assertEqual(format_chatml(example), expected)
            self.assertNotIn("<|im_start|>system", format_chatml(example))

    def test_single_example_splits_are_supported(self) -> None:
        train, validation, _ = build_corpus(seed=7, train_size=1, validation_size=1)
        self.assertEqual(len(train), 1)
        self.assertEqual(len(validation), 1)
        self.assertNotEqual(prompt_key(train[0]), prompt_key(validation[0]))
        self.assertNotEqual(train[0]["family"], validation[0]["family"])

    def test_written_jsonl_round_trips_and_hashes_match_file_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "generated"
            meta = write_corpus(output, seed=20261104, train_size=600, validation_size=100)
            self.assertEqual(json.loads((output / "meta.json").read_text(encoding="utf-8")), meta)
            self.assertTrue(meta["provenance"]["templates_authored_with_ai_assistance"])
            self.assertFalse(meta["provenance"]["external_model_api_inference"])
            self.assertEqual(meta["provenance"]["downloaded_datasets"], [])
            self.assertFalse(meta["provenance"]["pretrained_tokenizer"])
            self.assertNotIn("pretrained_model_outputs", meta["provenance"])
            for filename, expected in (("train.jsonl", self.train), ("validation.jsonl", self.validation)):
                with self.subTest(file=filename):
                    content = (output / filename).read_bytes()
                    self.assertTrue(content.endswith(b"\n"))
                    lines = content.decode("utf-8").splitlines()
                    self.assertEqual([json.loads(line) for line in lines], expected)
                    record = meta["files"][filename]
                    self.assertEqual(record["sha256"], hashlib.sha256(content).hexdigest())
                    self.assertEqual(record["bytes"], len(content))
                    self.assertEqual(record["examples"], len(expected))

    def test_written_dataset_is_reproducible_in_a_second_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "one"
            second = Path(temporary) / "two"
            first_meta = write_corpus(first, seed=91, train_size=60, validation_size=20)
            second_meta = write_corpus(second, seed=91, train_size=60, validation_size=20)
            self.assertEqual(first_meta, second_meta)
            for filename in ("train.jsonl", "validation.jsonl", "meta.json"):
                self.assertEqual((first / filename).read_bytes(), (second / filename).read_bytes())


if __name__ == "__main__":
    unittest.main()
