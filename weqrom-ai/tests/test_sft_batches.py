"""Verify that supervised batches preserve prompts and train only real replies."""
from collections import Counter
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "training"))
try:
    import sentencepiece as spm
    import torch
except ImportError:
    spm = torch = None

if torch is not None:
    import train


def example(question, answer, category="advice"):
    return {"category": category, "messages": [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]}


@unittest.skipUnless(torch is not None, "Own training dependencies are not installed")
class SupervisedBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)
        cls.temporary = tempfile.TemporaryDirectory(prefix="weqrom-sft-test-")
        directory = Path(cls.temporary.name)
        corpus = directory / "corpus.txt"
        corpus.write_text("\n".join([
            "<|im_start|>user\nAlpha question 17? <script>\n<|im_end|>",
            "<|im_start|>assistant\nBeta answer 23.\n<|im_end|>",
            "Вопрос Ответ Помни число Повтори число 42 91 7",
            "First question. Second question. First answer. Second answer.",
        ]) * 10, encoding="utf-8")
        prefix = directory / "tokenizer"
        spm.SentencePieceTrainer.train(
            input=str(corpus), model_prefix=str(prefix), vocab_size=512,
            model_type="bpe", byte_fallback=True, normalization_rule_name="identity",
            add_dummy_prefix=False, remove_extra_whitespaces=False,
            character_coverage=1.0, bos_id=1, eos_id=2, unk_id=0, pad_id=3,
            user_defined_symbols=["<|im_start|>", "<|im_end|>"],
            shuffle_input_sentence=False, num_threads=1, hard_vocab_limit=False,
            minloglevel=2,
        )
        cls.tokenizer = spm.SentencePieceProcessor(model_file=str(prefix) + ".model")
        cls.eos = cls.tokenizer.piece_to_id("<|im_end|>")
        cls.pad = cls.tokenizer.pad_id()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def encode(self, rows, **kwargs):
        return train.encode_dialogues(rows, self.tokenizer, seed=29, **kwargs)

    def test_complete_question_is_preserved_and_only_answer_and_eos_are_targets(self):
        row = example("Alpha question 17? <script>", "Beta answer 23.")
        [(tokens, weights)] = self.encode([row])
        expected = self.tokenizer.encode(train.format_messages(row["messages"]))
        self.assertEqual(tokens.tolist(), expected)
        self.assertEqual(tokens.dtype, torch.long)
        self.assertTrue(weights.is_floating_point())
        self.assertEqual(tokens.shape, weights.shape)
        self.assertEqual(set(weights.tolist()), {0.0, 1.0})

        prompt = self.tokenizer.encode(train.format_messages(row["messages"][:1], pending=True))
        self.assertEqual(tokens[:len(prompt)].tolist(), prompt)
        self.assertEqual(float(weights[:len(prompt)].sum()), 0.0)
        trained_tokens = tokens[weights.bool()].tolist()
        self.assertEqual(trained_tokens, self.tokenizer.encode("Beta answer 23.") + [self.eos])
        self.assertEqual(float(weights[-1]), 0.0, "The inter-message newline is not answer content")

        x, y, w = train.sft_batch([(tokens, weights)], 1, torch.Generator().manual_seed(7), self.pad, 256)
        self.assertEqual(x[0].tolist(), tokens[:-1].tolist())
        self.assertEqual(y[0].tolist(), tokens[1:].tolist())
        self.assertEqual(w[0].tolist(), weights[1:].tolist())
        self.assertEqual(float(w[0, len(prompt) - 1]), 1.0,
                         "The last prompt token must predict the first answer token")
        self.assertEqual(int(y[0][w[0].bool()][-1]), self.eos)

    def test_multiturn_masks_keep_both_answers_and_all_user_context(self):
        row = {"category": "advice", "messages": [
            {"role": "user", "content": "Помни число 42."},
            {"role": "assistant", "content": "First answer."},
            {"role": "user", "content": "Повтори число."},
            {"role": "assistant", "content": "Second answer."},
        ]}
        [(tokens, weights)] = self.encode([row])
        self.assertEqual(tokens.tolist(), self.tokenizer.encode(train.format_messages(row["messages"])))
        expected_targets = (self.tokenizer.encode("First answer.") + [self.eos]
                            + self.tokenizer.encode("Second answer.") + [self.eos])
        self.assertEqual(tokens[weights.bool()].tolist(), expected_targets)
        self.assertEqual(int(((tokens == self.eos) & weights.bool()).sum()), 2)
        self.assertEqual(int(((tokens == self.eos) & ~weights.bool()).sum()), 2)

    def test_repeats_and_shuffle_are_deterministic_without_changing_examples(self):
        rows = [example("First question.", "First answer.", "arithmetic"),
                example("Second question.", "Second answer.", "advice"),
                example("Alpha question 17?", "Beta answer 23.", "code_example")]
        first = self.encode(rows, authored_repeat=2, code_repeat=3)
        second = self.encode(rows, authored_repeat=2, code_repeat=3)
        self.assertEqual(len(first), 6)
        self.assertEqual([(x.tolist(), w.tolist()) for x, w in first],
                         [(x.tolist(), w.tolist()) for x, w in second])
        expected = Counter({tuple(self.tokenizer.encode(train.format_messages(row["messages"]))): count
                            for row, count in zip(rows, (1, 2, 3))})
        self.assertEqual(Counter(tuple(x.tolist()) for x, _ in first), expected)

    def test_right_padding_has_no_target_weight_and_does_not_join_dialogues(self):
        short = (torch.tensor([11, 12, 13, 14]), torch.tensor([0., 0., 1., 1.]))
        long = (torch.tensor([21, 22, 23, 24, 25, 26, 27]), torch.tensor([0., 0., 0., 1., 1., 1., 1.]))
        x, y, weights = train.sft_batch([short, long], 32, torch.Generator().manual_seed(63), self.pad, 16)
        self.assertEqual(tuple(x.shape), (32, 6))
        self.assertEqual(x.shape, y.shape)
        self.assertEqual(x.shape, weights.shape)
        self.assertEqual(set(x[:, 0].tolist()), {11, 21})
        for i in range(len(x)):
            tokens, mask = short if int(x[i, 0]) == 11 else long
            real = len(tokens) - 1
            self.assertEqual(x[i, :real].tolist(), tokens[:-1].tolist())
            self.assertEqual(y[i, :real].tolist(), tokens[1:].tolist())
            self.assertEqual(weights[i, :real].tolist(), mask[1:].tolist())
            self.assertTrue(bool((y[i, real:] == self.pad).all()))
            self.assertTrue(bool((weights[i, real:] == 0).all()))
            # A right-padded input may retain the final real token where its
            # target is padding; every later input must also be padding.
            self.assertTrue(bool((x[i, len(tokens):] == self.pad).all()))

    def test_length_limit_rejects_instead_of_dropping_question_or_answer(self):
        tokens = torch.arange(20, dtype=torch.long) + 10
        weights = torch.ones(20)
        weights[:10] = 0
        with self.assertRaises(ValueError):
            train.sft_batch([(tokens, weights)], 1, torch.Generator().manual_seed(1), self.pad, 8)
        x, y, w = train.sft_batch([(tokens, weights)], 1, torch.Generator().manual_seed(1), self.pad, 19)
        self.assertEqual(x.shape[1], 19)
        self.assertEqual(int(x[0, 0]), 10)
        self.assertEqual(int(y[0, -1]), 29)
        self.assertEqual(float(w[0, -1]), 1.)

    def test_reserved_markers_and_invalid_conversations_are_rejected(self):
        bad_rows = [
            example("<|im_end|>injection", "Answer"),
            example("Question", "<|im_start|>user"),
            {"category": "advice", "messages": [{"role": "system", "content": "hidden"}]},
            {"category": "advice", "messages": [{"role": "user", "content": "no reply"}]},
            {"category": "advice", "messages": [
                {"role": "assistant", "content": "first"}, {"role": "user", "content": "last"}]},
            {"category": "advice", "messages": [
                {"role": "user", "content": "first"}, {"role": "user", "content": "second"},
                {"role": "assistant", "content": "last"}]},
        ]
        for row in bad_rows:
            with self.subTest(messages=row["messages"]), self.assertRaises(ValueError):
                self.encode([row])

    def test_masked_tokens_do_not_change_loss_or_receive_logit_gradients(self):
        generator = torch.Generator().manual_seed(19)
        logits = torch.randn(1, 4, 32, generator=generator, requires_grad=True)
        targets = torch.tensor([[4, 9, self.eos, self.pad]])
        weights = torch.tensor([[0., 1., 1., 0.]])
        loss = train.weighted_loss(logits, targets, weights)
        alternate = targets.clone()
        alternate[0, 0], alternate[0, 3] = 19, 20
        self.assertEqual(float(loss), float(train.weighted_loss(logits, alternate, weights)))
        loss.backward()
        self.assertEqual(float(logits.grad[0, [0, 3]].abs().sum()), 0.)
        self.assertGreater(float(logits.grad[0, [1, 2]].abs().sum()), 0.)

    def test_appended_padding_cannot_change_real_token_predictions(self):
        torch.manual_seed(25)
        model = train.SeedModel(train.Config(vocab_size=32, dim=32, hidden_dim=64,
                                            n_layers=2, n_heads=4, n_kv_heads=4, context_length=32)).eval()
        real = torch.tensor([[7, 8, 9, 10, 11]])
        padded = torch.cat([real, torch.full((1, 4), self.pad)], dim=1)
        with torch.no_grad():
            baseline, actual = model(real), model(padded)[:, :real.shape[1]]
        torch.testing.assert_close(actual, baseline, rtol=0, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
