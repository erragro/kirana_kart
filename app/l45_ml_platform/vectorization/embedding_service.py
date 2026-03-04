"""
Embedding Service
=================

Production-grade embedding wrapper around OpenAI Embeddings API.

Responsibilities:
- Create embeddings for text inputs
- Retry with exponential backoff
- Batch processing with safe limits
- Deterministic model configuration
- Clean error propagation

No business logic.
No database logic.
No vector DB logic.

Pure embedding infrastructure layer.
"""

import os
import time
import logging
from typing import List
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path


# ============================================================
# CONFIGURATION
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[4]
load_dotenv(PROJECT_ROOT / ".env")

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1")

if not LLM_API_KEY:
    raise RuntimeError("LLM_API_KEY not found in environment.")

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"

MAX_INPUT_CHARS = 8000
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1

# Prevent overly large API requests
MAX_BATCH_SIZE = 100

EXPECTED_EMBEDDING_DIM = 3072


# ============================================================
# LOGGER
# ============================================================

logger = logging.getLogger("embedding_service")
logger.setLevel(logging.INFO)


# ============================================================
# EMBEDDING SERVICE
# ============================================================

class EmbeddingService:

    def __init__(self, model: str = DEFAULT_EMBEDDING_MODEL):

        self.model = model

        self.client = OpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_API_BASE_URL
        )

    # --------------------------------------------------------
    # PUBLIC: Single embedding
    # --------------------------------------------------------

    def create_embedding(self, text: str) -> List[float]:

        if not text or not text.strip():
            raise ValueError("Cannot create embedding for empty text.")

        text = self._sanitize_text(text)

        vector = self._retry_with_backoff(
            lambda: self._embed_call(text)
        )

        self._validate_embedding(vector)

        return vector

    # --------------------------------------------------------
    # PUBLIC: Batch embeddings
    # --------------------------------------------------------

    def create_embeddings_batch(self, texts: List[str]) -> List[List[float]]:

        if not texts:
            return []

        sanitized = [self._sanitize_text(t) for t in texts]

        results: List[List[float]] = []

        # Chunk batch requests
        for i in range(0, len(sanitized), MAX_BATCH_SIZE):

            batch = sanitized[i:i + MAX_BATCH_SIZE]

            logger.info(f"Embedding batch size={len(batch)}")

            embeddings = self._retry_with_backoff(
                lambda: self._embed_batch_call(batch)
            )

            for emb in embeddings:
                self._validate_embedding(emb)

            results.extend(embeddings)

        return results

    # --------------------------------------------------------
    # INTERNAL: OpenAI Call (Single)
    # --------------------------------------------------------

    def _embed_call(self, text: str) -> List[float]:

        response = self.client.embeddings.create(
            model=self.model,
            input=text,
            timeout=60
        )

        return response.data[0].embedding

    # --------------------------------------------------------
    # INTERNAL: OpenAI Call (Batch)
    # --------------------------------------------------------

    def _embed_batch_call(self, texts: List[str]) -> List[List[float]]:

        response = self.client.embeddings.create(
            model=self.model,
            input=texts,
            timeout=60
        )

        return [item.embedding for item in response.data]

    # --------------------------------------------------------
    # INTERNAL: Retry Logic
    # --------------------------------------------------------

    def _retry_with_backoff(self, func):

        attempt = 0
        backoff = INITIAL_BACKOFF_SECONDS

        while attempt < MAX_RETRIES:

            try:
                return func()

            except Exception as e:

                attempt += 1

                if attempt >= MAX_RETRIES:
                    logger.error(
                        f"Embedding failed after {MAX_RETRIES} attempts."
                    )
                    raise e

                logger.warning(
                    f"Embedding attempt {attempt} failed. "
                    f"Retrying in {backoff}s..."
                )

                time.sleep(backoff)
                backoff *= 2

    # --------------------------------------------------------
    # INTERNAL: Sanitize Input
    # --------------------------------------------------------

    def _sanitize_text(self, text: str) -> str:

        text = text.strip()

        if len(text) > MAX_INPUT_CHARS:

            logger.warning(
                "Embedding input exceeded max size. Truncating."
            )

            text = text[:MAX_INPUT_CHARS]

        return text

    # --------------------------------------------------------
    # INTERNAL: Embedding Validation
    # --------------------------------------------------------

    def _validate_embedding(self, vector: List[float]):

        if len(vector) != EXPECTED_EMBEDDING_DIM:

            raise RuntimeError(
                f"Unexpected embedding dimension: {len(vector)}"
            )