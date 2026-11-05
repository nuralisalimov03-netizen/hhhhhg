"""Independent positive/negative checks for the raw-model benchmark scorer.

No weights, inference or model-generated code execution is required. Code
checks apply to the benchmark's documented small subset, not arbitrary programs.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from benchmark_v2 import grade, numeric_answer, prompt_for


def row(task: dict, answer: str = "", question: str = "Проверочный вопрос") -> dict:
    return {
        "id": "independent-test-fixture",
        "category": "test-fixture",
        "messages": [{"role": "user", "content": question}, {"role": "assistant", "content": answer}],
        "task": task,
    }


def fenced(code: str, language: str = "javascript") -> str:
    return f"```{language}\n{code}\n```"


class BenchmarkV2Tests(unittest.TestCase):
    def assert_grade(self, example: dict, text: str, passed: bool, metric: str | None = None) -> dict:
        result = grade(example, text)
        self.assertIsInstance(result, dict)
        self.assertIs(type(result["passed"]), bool)
        self.assertEqual(result["passed"], passed, result)
        self.assertIsInstance(result["metric"], str)
        self.assertTrue(result["metric"])
        if metric is not None:
            self.assertEqual(result["metric"], metric)
        return result

    def test_all_one_hundred_frozen_gold_references_pass(self) -> None:
        frozen = Path("/workspace/.chits-tools/weqrom-corpus-v2/test.jsonl")
        if frozen.is_file():
            examples = [json.loads(line) for line in frozen.read_text(encoding="utf-8").splitlines()]
        else:
            # Reconstruct the deterministic public corpus without local model assets.
            from corpus import write_corpus
            from curriculum_v2 import build
            with tempfile.TemporaryDirectory() as temporary:
                old = Path(temporary) / "v1"
                write_corpus(old)
                examples = build(train_size=1, validation_size=1, test_size=100, old_data_dir=old)["test"]
        self.assertEqual(len(examples), 100)
        for example in examples:
            with self.subTest(id=example["id"], task=example["task"]["kind"]):
                self.assert_grade(example, example["messages"][-1]["content"], True)

    def test_numeric_parser_accepts_explicit_final_numbers(self) -> None:
        for text, expected in (
            ("Ответ:\n-28", -28.0),
            ("Ответ: x = 6.", 6.0),
            ("x = 6", 6.0),
            ("42", 42.0),
            ("Ответ: 2,5", 2.5),
            ("Ответ: 5. Затем проверка. Ответ: 6.", 6.0),
        ):
            with self.subTest(text=text):
                self.assertEqual(numeric_answer(text), expected)

    def test_incidental_numbers_are_not_a_final_answer(self) -> None:
        for text in ("", "Числа в условии: 2 и 3; я не знаю ответ.", "Проверка: 5 + 5 = 10.", "Здесь встречается 42.",
                     "Ответ: 4+3", "Ответ: 4/2", "Ответ: 4 * 3", "Ответ: 4 ^ 2", "Ответ: 4.2abc", "Ответ: 4. Ответ: 4+3"):
            with self.subTest(text=text):
                self.assertIsNone(numeric_answer(text))

    def test_arithmetic_wrong_final_answer_fails_even_if_expected_number_occurs(self) -> None:
        example = row({"kind": "arithmetic", "operation": "add", "operands": [2, 3], "result": 5})
        self.assert_grade(example, "Ответ:\n5", True)
        for wrong in ("Ответ: 50", "2 + 3 = 5. Ответ: 6.", "Ответ: 5. Ответ: 6.", "В задаче есть 5, но я не знаю решения."):
            with self.subTest(text=wrong):
                self.assert_grade(example, wrong, False)

    def test_equation_solution_is_scored_from_the_final_answer(self) -> None:
        example = row({"kind": "linear", "operands": [7, -20, 6], "rhs": 22, "result": 6})
        self.assert_grade(example, "Ответ:\nx = 6", True)
        self.assert_grade(example, "В условии 7x - 20 = 22. Ответ: x = 7.", False)

    def test_copy_is_exact_and_does_not_accept_a_substring(self) -> None:
        example = row({"kind": "copy", "field": "identifier", "value": "score17"})
        self.assert_grade(example, "Ответ:\nscore17", True)
        for wrong in ("Ответ:\nscore170", "Ответ:\nscore18", "Было score17, теперь score18."):
            self.assert_grade(example, wrong, False)

    def test_multiturn_uses_the_final_target_without_substring_matches(self) -> None:
        example = row({"kind": "multiturn", "target": "21"})
        example["messages"] = [
            {"role": "user", "content": "Моё число 21."},
            {"role": "assistant", "content": "Запомнил число 21."},
            {"role": "user", "content": "Какое число я назвал?"},
            {"role": "assistant", "content": "Ответ:\n21"},
        ]
        self.assert_grade(example, "Ответ:\n21", True)
        self.assert_grade(example, "Ответ:\n210", False)
        self.assert_grade(example, "Раньше было 21. Ответ:\n22", False)

    def test_html_card_requires_all_slots_in_actual_valid_markup(self) -> None:
        example = row({"kind": "code", "topic": "card", "slots": {"title": "Профиль 7", "color": "green", "padding": 12}})
        valid = '<section style="color:green;padding:12px"><h2>Профиль 7</h2></section>'
        self.assert_grade(example, fenced(valid, "html"), True, "code_slots_satisfied")
        for wrong in (
            valid.replace("Профиль 7", "Профиль 8"),
            valid.replace("green", "blue"),
            valid.replace("12px", "13px"),
            valid.replace("</section>", ""),
            valid.replace("</section>", "</article>"),
        ):
            with self.subTest(code=wrong):
                self.assert_grade(example, fenced(wrong, "html"), False, "code_slots_satisfied")
        self.assert_grade(example, "Профиль 7, green, 12px.\n" + fenced(valid.replace("green", "red"), "html"), False)

    def test_counter_requires_initial_step_and_matching_dom_bindings(self) -> None:
        example = row({"kind": "code", "topic": "counter", "slots": {"initial": 7, "step": 3}})
        valid = (
            '<button id="b">7</button>\n<script>\nlet n=7;\n'
            "const b=document.getElementById('b');\n"
            'b.onclick=()=>{n+=3;b.textContent=n;};\n</script>'
        )
        self.assert_grade(example, fenced(valid, "html"), True, "code_slots_satisfied")
        for wrong in (
            valid.replace(">7</button>", ">8</button>"),
            valid.replace("let n=7", "let n=8"),
            valid.replace("n+=3", "n+=4"),
            valid.replace("n+=3", "n-=3"),
            valid.replace("getElementById('b')", "getElementById('other')"),
            valid.replace("b.textContent=n", "other.textContent=n"),
            valid.replace("b.textContent=n;", ""),
            valid.replace("const b=", "const n=").replace("b.onclick", "n.onclick").replace("b.textContent", "n.textContent"),
        ):
            with self.subTest(code=wrong):
                self.assert_grade(example, fenced(wrong, "html"), False, "code_slots_satisfied")

    def test_function_requires_correct_operator_identifier_input_and_syntax(self) -> None:
        example = row({"kind": "code", "topic": "function", "slots": {"name": "add12", "increment": 4, "input": 7}})
        valid = 'function add12(x){return x+4;}\nconsole.log(add12(7));'
        self.assert_grade(example, fenced(valid), True, "code_slots_satisfied")
        for wrong in (
            valid.replace("x+4", "x-4"),
            valid.replace("x+4", "x+5"),
            valid.replace("add12(7)", "add12(8)"),
            valid.replace("add12", "add13"),
            valid.replace(";}", ";"),
            '// function add12(x){return x+4;}\nconsole.log(add12(7));',
            valid.replace('(x)', '(return)').replace('return x+', 'return return+'),
            valid.replace('return x+', 'return\nx+'),
        ):
            with self.subTest(code=wrong):
                self.assert_grade(example, fenced(wrong), False, "code_slots_satisfied")
        self.assert_grade(example, "Имя add12, прибавление 4, аргумент 7.", False)

    def test_card_title_entities_are_decoded_without_allowing_nested_markup(self) -> None:
        example = row({'kind':'code','topic':'card','slots':{'title':'A & <B>','color':'green','padding':2}})
        good = '<section style="color:green;padding:2px"><h2>A &amp; &lt;B&gt;</h2></section>'
        self.assert_grade(example, fenced(good,'html'), True)
        self.assert_grade(example, fenced(good.replace('&lt;B&gt;','<B></B>'),'html'), False)

    def test_authored_reference_check_does_not_accept_an_unrelated_answer(self) -> None:
        example = row({"kind": "authored", "topic": "html"}, "HTML задаёт структуру страницы.")
        self.assert_grade(example, "HTML задаёт структуру страницы.", True)
        self.assert_grade(example, "Я не знаю.", False)

    def test_native_prompt_uses_history_without_the_reference_answer(self) -> None:
        example = row({'kind':'multiturn','target':'21'})
        example['messages'] = [
            {'role':'user','content':'Моё число 21.'},
            {'role':'assistant','content':'Запомнил.'},
            {'role':'user','content':'Какое число?'},
            {'role':'assistant','content':'SECRET_REFERENCE_21'},
        ]
        self.assertEqual(prompt_for(example),
                         '<|im_start|>user\nМоё число 21.<|im_end|>\n'
                         '<|im_start|>assistant\nЗапомнил.<|im_end|>\n'
                         '<|im_start|>user\nКакое число?<|im_end|>\n'
                         '<|im_start|>assistant\n')
        self.assertNotIn('SECRET_REFERENCE',prompt_for(example))


if __name__ == "__main__":
    unittest.main()
