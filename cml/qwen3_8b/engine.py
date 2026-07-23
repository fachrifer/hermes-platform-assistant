"""Transformers-based FP16 engine for Qwen3 8B on NVIDIA V100 (sm_70)."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("hermes.cml.qwen")


class TransformersEngine:
    """Lazy-load Qwen with torch.float16 for Volta GPUs."""

    def __init__(self, model_id: str | None = None, model_path: str | None = None):
        self.model_id = model_id or os.getenv("QWEN_MODEL_ID", "Qwen/Qwen3-8B")
        self.model_path = model_path or os.getenv("QWEN_MODEL_PATH") or self.model_id
        self._tokenizer: Any = None
        self._model: Any = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        logger.info("Loading Qwen model from %s (FP16)", self.model_path)
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_path, trust_remote_code=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_path,
            torch_dtype=torch.float16,
            device_map="auto",
            trust_remote_code=True,
        )
        self._model.eval()

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 256,
        temperature: float = 0.2,
    ) -> str:
        import torch

        self._ensure_loaded()
        assert self._tokenizer is not None and self._model is not None
        if hasattr(self._tokenizer, "apply_chat_template"):
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
        return self._tokenizer.decode(generated, skip_special_tokens=True).strip()
