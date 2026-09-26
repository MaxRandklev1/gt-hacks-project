"""Worker launched only in the isolated training venv; never imports ComfyUI."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import shutil
import runpy
import sys
import time

from backend import ProfileStore, read_json, write_json, within


BUCKETS = "512,384;512,448;512,512;448,512;384,512"


def dataset_cache_path(training, output):
    # datasets repeats its absolute cache path in a lock filename. Putting that
    # cache below profile/runs/output exceeds Windows' unprefixed path limit.
    # A short, unique per-run directory remains inside the isolated training tree.
    key = hashlib.sha256(str(Path(output).resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(training) / ".dc" / key


def build_arguments(training, dataset, output, trigger, steps):
    return [str(training / ".venv/Scripts/python.exe"), str(training / "trainer/train_identity_qwen21.py"),
        "--pretrained_model_name_or_path", str(training / "models/Qwen-Image-2.1"),
        "--dataset_name", str(dataset), "--image_column", "image", "--caption_column", "text",
        "--cache_dir", str(dataset_cache_path(training, output)),
        "--instance_prompt", f"a photo of {trigger}, a person", "--output_dir", str(output),
        "--mixed_precision", "bf16", "--bnb_quantization_config_path", str(training / "trainer/nf4.json"),
        "--text_encoder_4bit", "--offload", "--cache_latents", "--gradient_checkpointing", "--use_8bit_adam",
        "--train_batch_size", "1", "--gradient_accumulation_steps", "1", "--resolution", "512",
        "--use_aspect_ratio_buckets", "--aspect_ratio_buckets", BUCKETS, "--center_crop",
        "--rank", "16", "--lora_alpha", "16", "--learning_rate", "1e-4", "--lr_scheduler", "constant",
        "--lr_warmup_steps", "0", "--max_train_steps", str(steps),
        "--checkpointing_steps", "1" if steps == 2 else "100", "--dataloader_num_workers", "0", "--seed", "42",
        "--report_to", "none", "--skip_final_inference"]


def run(config_path, job_path, launch_token):
    store = ProfileStore(config_path)
    job_path = Path(job_path).resolve()
    if not job_path.is_relative_to(store.root) or job_path.name != "job.json":
        raise ValueError("Worker job must be a local profile job manifest.")
    # Windows venv launchers have a different PID from this interpreter. Validate
    # the private reservation nonce, then register the actual GPU-owner PID.
    for _ in range(100):
        job = read_json(job_path)
        if job.get("launch_token") != launch_token:
            raise RuntimeError("Training reservation token does not match.")
        if job["status"] == "running":
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Parent did not complete the training reservation.")
    job["pid"] = os.getpid()
    write_json(job_path, job)
    profile = store.load(job["profile_id"])
    directory = store.profile_dir(profile["id"])
    output = within(directory, job["output_relative"])
    dataset = job_path.parent / "dataset"
    try:
        profile["training"] = {"status": "running", "job_id": job["job_id"]}
        store.save(profile)
        dataset.mkdir(exist_ok=False)
        if output.exists():
            raise RuntimeError("Refusing to overwrite an existing training output.")
        selected = set(job["selected_photo_ids"])
        snapshot = []
        for photo in profile["photos"]:
            if photo["id"] not in selected:
                continue
            target = dataset / (photo["id"] + ".png")
            shutil.copyfile(store.photo_path(profile, photo["id"]), target)
            snapshot.append({"file_name": target.name,
                             "text": photo.get("caption") or f"photo of {profile['trigger']}, a person"})
        if not snapshot:
            raise RuntimeError("No selected photos were available.")
        with (dataset / "metadata.jsonl").open("w", encoding="utf-8") as stream:
            for row in snapshot:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        training = store.config["training_root"]
        arguments = build_arguments(training, dataset, output, profile["trigger"], job["max_steps"])
        write_json(job_path.parent / "launch.json", {"arguments": arguments, "dataset": snapshot, "buckets_height_width": BUCKETS})
        old_arguments = sys.argv
        old_path = list(sys.path)
        try:
            sys.argv = arguments[1:]
            sys.path.insert(0, str(training / "trainer"))
            with (job_path.parent / "training.log").open("w", encoding="utf-8", buffering=1) as log:
                with redirect_stdout(log), redirect_stderr(log):
                    try:
                        runpy.run_path(arguments[1], run_name="__main__")
                    except SystemExit as exit_request:
                        if exit_request.code not in (None, 0):
                            raise RuntimeError(f"Trainer exited with code {exit_request.code}. Inspect the local training log.") from exit_request
        finally:
            sys.argv = old_arguments
            sys.path[:] = old_path
        health = read_json(output / "training_health.json")
        adapter = output / "pytorch_lora_weights.safetensors"
        if health.get("status") != "completed" or health.get("completed_updates") != job["max_steps"] or not adapter.is_file():
            raise RuntimeError("Trainer did not produce a verified complete adapter.")
        job.update(status="completed", finished_at=time.time(), error=None)
        profile = store.load(profile["id"])
        profile["training"] = {"status": "completed", "job_id": job["job_id"]}
        # A two-update check proves mechanics, not a useful learned identity.
        if job["max_steps"] != 2:
            profile["latest_successful"] = {"job_id": job["job_id"], "steps": job["max_steps"],
                "completed_at": job["finished_at"], "adapter_relative": str(adapter.relative_to(directory)).replace("\\", "/")}
        store.save(profile)
        write_json(job_path, job)
    except Exception as error:
        job.update(status="failed", finished_at=time.time(), error=str(error))
        write_json(job_path, job)
        profile = store.load(profile["id"])
        profile["training"] = {"status": "failed", "job_id": job["job_id"]}
        store.save(profile)
        raise
    # The server clears the reservation only after this entire process exits,
    # including CUDA teardown. Never release GPU ownership from inside training.


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--launch-token", required=True)
    args = parser.parse_args()
    run(args.config, args.job, args.launch_token)
