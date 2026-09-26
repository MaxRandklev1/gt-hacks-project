"""Check the full quantized encoder while the independent DiT download finishes."""
import json
import os
from pathlib import Path
import time

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'

import torch
from diffusers import QwenImage21Pipeline
from transformers import BitsAndBytesConfig, Qwen3VLForConditionalGeneration

root = Path(__file__).resolve().parent
model = str(root / 'models' / 'Qwen-Image-2.1')
start = time.monotonic()
encoder = Qwen3VLForConditionalGeneration.from_pretrained(
    model, subfolder='text_encoder', torch_dtype=torch.bfloat16,
    attn_implementation='sdpa', low_cpu_mem_usage=True,
    quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16),
    device_map={'': 'cuda:0'}, local_files_only=True,
)
pipeline = QwenImage21Pipeline.from_pretrained(model, text_encoder=encoder, transformer=None, vae=None, torch_dtype=torch.bfloat16, local_files_only=True)
with torch.no_grad():
    embeddings, mask, image_mask = pipeline.encode_prompt(prompt='photo of j0n_person, a man with a beard wearing a gray hoodie')
    assert torch.isfinite(embeddings).all(), 'Nonfinite text embeddings'
report = {'status': 'passed', 'embedding_shape': list(embeddings.shape), 'embedding_dtype': str(embeddings.dtype), 'embedding_device': str(embeddings.device), 'peak_cuda_gib': torch.cuda.max_memory_allocated() / 2**30, 'elapsed_seconds': time.monotonic() - start}
(root / 'text-encoder-health.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report), flush=True)
