"""Import installed features without contacting providers or opening devices."""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FEATURES = {
    "base": ["fastapi", "uvicorn", "google.genai", "websockets", "httpx", "pythainlp", "numpy"],
    "local-search": ["sentence_transformers"],
    "face": ["insightface", "onnxruntime", "cv2", "skimage"],
    "wake": ["sherpa_onnx", "sentencepiece"],
    "browser": ["playwright.async_api"],
    "hardware": ["broadlink", "sounddevice"],
    "documents": ["pymupdf", "reportlab"],
    "websearch": ["ddgs"],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-extras", action="store_true")
    parser.add_argument("--local-model", type=Path, help="optional existing local model folder; no downloads")
    args = parser.parse_args()
    for feature, modules in FEATURES.items():
        if feature != "base" and not args.all_extras:
            continue
        for module in modules:
            importlib.import_module(module)
        if feature == "wake":
            # A successful Python import alone misses the absent core DLL bug.
            core = importlib.metadata.distribution("sherpa-onnx-core")
            if not any(str(f).endswith("sherpa-onnx-c-api.dll") and core.locate_file(f).is_file()
                       for f in core.files or []):
                raise RuntimeError("sherpa-onnx-core DLL is missing")
        print(f"{feature}: imports PASS")
    if args.local_model:
        if not args.local_model.is_dir():
            parser.error("--local-model must be an existing local directory")
        from sentence_transformers import SentenceTransformer
        import numpy as np
        model = SentenceTransformer(str(args.local_model.resolve()), local_files_only=True)
        vectors = model.encode(["hello", "hello"])
        assert vectors.ndim == 2 and vectors.shape[0] == 2 and np.isfinite(vectors).all()
        assert np.allclose(vectors[0], vectors[1], atol=1e-5)
        print("local-search: actual offline encoding PASS")
    importlib.import_module("app.main")  # import only; no FastAPI lifespan/startup
    print("app.main: import PASS (no startup, provider calls or device access)")


if __name__ == "__main__":
    main()
