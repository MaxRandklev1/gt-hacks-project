"""Local diagnostics for a real adapter update, independent of apparent loss."""
import json
import math
import os
import time
from pathlib import Path

import torch


class TrainingHealth:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.report_path = self.output_dir / "training_health.json"
        self.started = time.monotonic()
        self.report = {"status": "preparing", "events": [], "updates": []}
        self.previous = {}

    def _write(self):
        temp = self.report_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temp, self.report_path)

    @staticmethod
    def _b_parameters(model):
        return {name: p for name, p in model.named_parameters() if p.requires_grad and ".lora_B." in name}

    def memory_event(self, name):
        entry = {"event": name, "elapsed_seconds": round(time.monotonic() - self.started, 3)}
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            entry.update(
                cuda_allocated_gib=torch.cuda.memory_allocated() / 2**30,
                cuda_reserved_gib=torch.cuda.memory_reserved() / 2**30,
                cuda_peak_allocated_gib=torch.cuda.max_memory_allocated() / 2**30,
                cuda_free_gib=free / 2**30,
                cuda_total_gib=total / 2**30,
            )
        try:
            import psutil
            entry["process_rss_gib"] = psutil.Process().memory_info().rss / 2**30
            entry["system_available_gib"] = psutil.virtual_memory().available / 2**30
        except ImportError:
            pass
        self.report["events"].append(entry)
        self._write()

    def start(self, model, step):
        params = self._b_parameters(model)
        if not params:
            raise RuntimeError("No trainable LoRA-B tensors were found.")
        self.previous = {name: p.detach().float().cpu().clone() for name, p in params.items()}
        self.report.update(status="training", initial_step=step, lora_b_tensor_count=len(params))
        self.report["trainable_parameters"] = sum(p.numel() for p in model.parameters() if p.requires_grad)
        self.memory_event("training_ready")

    @staticmethod
    def _check_step(step):
        return step <= 2 or step % 10 == 0

    def check_gradients(self, model, step):
        if not self._check_step(step):
            return
        nonzero_b = 0
        total_b_squared_norm = 0.0
        for name, param in model.named_parameters():
            if not param.requires_grad or param.grad is None:
                continue
            grad = param.grad.detach()
            if not torch.isfinite(grad).all():
                self.report.update(status="failed", failure=f"Nonfinite gradient: {name}", failed_step=step)
                self._write()
                raise FloatingPointError(self.report["failure"])
            if ".lora_B." in name:
                norm = float(grad.float().norm())
                total_b_squared_norm += norm * norm
                nonzero_b += int(norm > 0)
        if nonzero_b == 0:
            self.report.update(status="failed", failure="All LoRA-B gradients are zero or absent", failed_step=step)
            self._write()
            raise RuntimeError(self.report["failure"])
        self.pending = {"step": step, "nonzero_b_gradient_tensors": nonzero_b,
                        "b_gradient_norm": math.sqrt(total_b_squared_norm)}

    def after_step(self, model, step, loss):
        if not self._check_step(step):
            return
        params = self._b_parameters(model)
        current = {name: p.detach().float().cpu().clone() for name, p in params.items()}
        if set(current) != set(self.previous):
            raise RuntimeError("Trainable adapter tensor names changed during training.")
        changed = nonzero = 0
        delta_squared_norm = 0.0
        for name, tensor in current.items():
            if not torch.isfinite(tensor).all():
                raise FloatingPointError(f"Nonfinite LoRA-B weight after update: {name}")
            difference = tensor - self.previous[name]
            changed += int(not torch.equal(tensor, self.previous[name]))
            nonzero += int(bool(torch.count_nonzero(tensor)))
            delta_squared_norm += float(difference.norm()) ** 2
        if changed == 0 or nonzero == 0:
            self.report.update(status="failed", failure="Optimizer did not change LoRA-B weights", failed_step=step)
            self._write()
            raise RuntimeError(self.report["failure"])
        update = dict(self.pending)
        update.update(loss=loss, changed_b_tensors=changed, nonzero_b_tensors=nonzero,
                      b_delta_norm=math.sqrt(delta_squared_norm))
        self.report["updates"].append(update)
        self.previous = current
        self.memory_event(f"checked_update_{step}")

    def finish(self, adapter_path, step):
        from safetensors.torch import load_file
        state = load_file(str(adapter_path), device="cpu")
        b_weights = {k: v for k, v in state.items() if ".lora_B." in k}
        if not b_weights or not all(bool(torch.isfinite(v).all()) for v in state.values()):
            raise RuntimeError("Saved adapter contains missing LoRA-B tensors or nonfinite weights.")
        if not any(bool(torch.count_nonzero(v)) for v in b_weights.values()):
            raise RuntimeError("Saved adapter has only zero LoRA-B tensors.")
        self.report.update(status="completed", completed_updates=step,
                           saved_adapter=str(adapter_path), saved_b_tensor_count=len(b_weights))
        self.memory_event("saved_adapter_verified")
