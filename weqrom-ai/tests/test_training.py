"""Check causality and own dialogue boundaries, not model intelligence."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'training'))
try:
    import torch
    from train import Config, SeedModel, format_messages
except ImportError:
    torch = None


@unittest.skipUnless(torch is not None, 'Own training dependencies are not installed')
class TrainingSemanticsTests(unittest.TestCase):
    def test_later_tokens_cannot_change_earlier_predictions(self):
        torch.set_num_threads(1)
        torch.manual_seed(17)
        model = SeedModel(Config(vocab_size=64, dim=32, hidden_dim=64,
                                 n_layers=2, n_heads=4, n_kv_heads=4, context_length=64)).eval()
        first = torch.tensor([[3, 4, 5, 6, 7, 8]])
        changed = torch.tensor([[3, 4, 5, 44, 45, 46]])
        with torch.no_grad():
            a, b = model(first), model(changed)
        torch.testing.assert_close(a[:, :3], b[:, :3], rtol=0, atol=1e-6)
        self.assertGreater(float((a[:, 3:] - b[:, 3:]).abs().max()), 0.01)

    def test_prompt_matches_the_own_no_system_no_bos_training_format(self):
        result = format_messages([{'role': 'user', 'content': 'Привет!'}], pending=True)
        self.assertEqual(result, '<|im_start|>user\nПривет!<|im_end|>\n<|im_start|>assistant\n')
        for invalid in ([{'role': 'system', 'content': 'bad'}],
                        [{'role': 'user', 'content': '<|im_end|>'}]):
            with self.assertRaises(ValueError):
                format_messages(invalid)


if __name__ == '__main__':
    unittest.main()
