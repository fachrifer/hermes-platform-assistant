"""Transformers-based FP16 engine for Qwen3 8B on CML PBJ GPU runtimes.

Target runtime: PBJ Workbench · Python 3.12 · Nvidia GPU (CUDA 12.5).
Weights load as torch.float16 for V100 (sm_70) and newer GPUs.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("hermes.cml.qwen")

_DEFAULT_LOCAL_PATHS = (
    "/home/cdsw/models/Qwen3-8B",
    "/home/cdsw/models/Qwen/Qwen3-8B",
)


def _resolve_model_path(model_id: str, explicit_path: str | None) -> str:
    """Prefer explicit / local disk weights over Hugging Face hub download."""
    if explicit_path:
        return explicit_path
    env_path = os.getenv("QWEN_MODEL_PATH", "").strip()
    if env_path:
        return env_path
    for candidate in _DEFAULT_LOCAL_PATHS:
        cfg = Path(candidate) / "config.json"
        if cfg.is_file():
            logger.info("Using local weights at %s (skip HF download)", candidate)
            return candidate
    return model_id


class TransformersEngine:
    """Lazy-load Qwen with torch.float16 (V100-safe; works on newer GPUs too)."""

    def __init__(self, model_id: str | None = None, model_path: str | None = None):
        self.model_id = model_id or os.getenv("QWEN_MODEL_ID", "Qwen/Qwen3-8B")
        self.model_path = _resolve_model_path(self.model_id, model_path)
        # Qwen3 thinking/reasoning — default OFF for low latency.
        thinking = os.getenv("QWEN_ENABLE_THINKING", "0").strip().lower()
        self.enable_thinking = thinking in {"1", "true", "yes", "on"}
        self._tokenizer: Any = None
        self._model: Any = None
        self._load_error: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @property
    def is_loading(self) -> bool:
        return getattr(self, "_is_loading", False)

    def preload(self) -> None:
        """Load tokenizer + weights now (call at Application startup)."""
        self._is_loading = True
        self._load_error = None
        try:
            self._ensure_loaded()
        finally:
            self._is_loading = False

    def _validate_local_path(self) -> None:
        path = Path(self.model_path)
        if not path.exists():
            # Hugging Face hub id (org/name) is not a local path — skip check.
            if "/" in self.model_path and not self.model_path.startswith("/"):
                return
            raise FileNotFoundError(
                f"QWEN_MODEL_PATH not found: {self.model_path}. "
                "Upload weights (folder with config.json) or set QWEN_MODEL_ID "
                "if Hugging Face egress is allowed."
            )
        if path.is_dir() and not (path / "config.json").is_file():
            raise FileNotFoundError(
                f"Local model dir missing config.json: {self.model_path}. "
                "That folder is not a valid Hugging Face weights directory."
            )

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return

        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer

        version = transformers.__version__
        major_minor = tuple(int(p) for p in version.split(".")[:2])
        if major_minor < (4, 51):
            raise RuntimeError(
                f"Qwen3 needs transformers>=4.51.0, but this kernel has {version} "
                f"(from {transformers.__file__}). In CML Session run:\n"
                '  !python -m pip install -U "transformers==4.51.3"\n'
                "then Kernel → Restart, then create a NEW TransformersEngine()."
            )

        if torch.cuda.is_available():
            major, minor = torch.cuda.get_device_capability(0)
            arches = torch.cuda.get_arch_list()
            gpu_name = torch.cuda.get_device_name(0)
            logger.info(
                "GPU %s capability sm_%s%s arch_list=%s",
                gpu_name,
                major,
                minor,
                arches,
            )
            if (major, minor) == (7, 0) and not any(
                a == "sm_70" or a.startswith("sm_70") for a in arches
            ):
                raise RuntimeError(
                    f"GPU is {gpu_name} (sm_70 / V100) but this PyTorch build "
                    f"({torch.__version__}) has no sm_70 kernels (arch_list={arches}). "
                    "cu128/cu130 drop V100. In a Nvidia GPU Session run:\n"
                    "  !pip uninstall -y torch torchvision torchaudio\n"
                    "  !pip install torch==2.6.0 --index-url "
                    "https://download.pytorch.org/whl/cu126\n"
                    "Then Kernel → Restart. Verify: "
                    "torch.cuda.get_arch_list() includes 'sm_70'."
                )

        # Allow retry after upgrading packages in the same notebook process.
        self._load_error = None

        try:
            self._validate_local_path()
            token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN") or None
            logger.info(
                "Loading Qwen model from %s (FP16, transformers=%s, token=%s)",
                self.model_path,
                version,
                "set" if token else "none",
            )
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_path, trust_remote_code=True, token=token
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_path,
                torch_dtype=torch.float16,
                device_map="auto",
                trust_remote_code=True,
                token=token,
            )
            self._model.eval()
            logger.info("Qwen model loaded successfully from %s", self.model_path)
        except Exception as exc:  # noqa: BLE001
            self._load_error = (
                f"Failed to load model from {self.model_path!r}: {exc}. "
                "If CML cannot reach huggingface.co, download weights elsewhere "
                "and set QWEN_MODEL_PATH to a local folder containing config.json."
            )
            logger.exception(self._load_error)
            raise RuntimeError(self._load_error) from exc

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 256,
        temperature: float = 0.2,
    ) -> str:
        import re

        import torch

        self._ensure_loaded()
        assert self._tokenizer is not None and self._model is not None
        if hasattr(self._tokenizer, "apply_chat_template"):
            try:
                prompt = self._tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=self.enable_thinking,
                )
            except TypeError:
                # Older chat templates may not accept enable_thinking.
                prompt = self._tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
        else:
            prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages) + "\nassistant:"
        inputs = self._tokenizer(prompt, return_tensors="pt")
        inputs = {key: value.to(self._model.device) for key, value in inputs.items()}
        with torch.no_grad():
            output = self._model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                do_sample=temperature > 0,
                temperature=max(temperature, 1e-5),
                pad_token_id=getattr(self._tokenizer, "eos_token_id", None),
            )
        generated = output[0][inputs["input_ids"].shape[-1] :]
        text = self._tokenizer.decode(generated, skip_special_tokens=True).strip()
        # Strip any leftover think blocks if the model still emits them.
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()
        return text
