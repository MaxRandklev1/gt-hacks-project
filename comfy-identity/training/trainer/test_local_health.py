"""CPU-only tests; no model files, photos, GPU allocation, or network required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
from safetensors.torch import save_file

from local_health import TrainingHealth


class TinyAdapter(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.layer = torch.nn.Module()
        self.layer.lora_B = torch.nn.ModuleDict({"default": torch.nn.Linear(2, 2, bias=False)})
        torch.nn.init.zeros_(self.layer.lora_B["default"].weight)


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cuda_patch = patch("torch.cuda.is_available", return_value=False)
        self.cuda_patch.start()
        self.addCleanup(self.cuda_patch.stop)
        self.model = TinyAdapter()
        self.health = TrainingHealth(self.tmp.name)
        self.health.start(self.model, 0)

    def test_two_real_updates_and_saved_adapter(self):
        optimizer = torch.optim.AdamW(self.model.parameters(), lr=1e-3)
        for step in (1, 2):
            loss = (self.model.layer.lora_B["default"](torch.ones(1, 2)) - 1).square().mean()
            loss.backward()
            self.health.check_gradients(self.model, step)
            optimizer.step()
            self.health.after_step(self.model, step, float(loss.detach()))
            optimizer.zero_grad()
        adapter = Path(self.tmp.name) / "pytorch_lora_weights.safetensors"
        save_file({k: v.detach().contiguous() for k, v in self.model.named_parameters()}, str(adapter))
        self.health.finish(adapter, 2)
        self.assertEqual(self.health.report["status"], "completed")
        self.assertEqual(len(self.health.report["updates"]), 2)
        self.assertGreater(self.health.report["updates"][1]["b_delta_norm"], 0)

    def test_zero_gradients_fail(self):
        for param in self.model.parameters():
            param.grad = torch.zeros_like(param)
        with self.assertRaisesRegex(RuntimeError, "zero or absent"):
            self.health.check_gradients(self.model, 1)

    def test_nonfinite_gradients_fail(self):
        for param in self.model.parameters():
            param.grad = torch.full_like(param, float("nan"))
        with self.assertRaises(FloatingPointError):
            self.health.check_gradients(self.model, 1)

    def test_unchanged_weights_fail_despite_nonzero_gradients(self):
        for param in self.model.parameters():
            param.grad = torch.ones_like(param)
        self.health.check_gradients(self.model, 1)
        with self.assertRaisesRegex(RuntimeError, "did not change"):
            self.health.after_step(self.model, 1, 1.0)


if __name__ == "__main__":
    unittest.main()
