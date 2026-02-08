import os
import logging
import faiss
import numpy as np
import pickle
from openai import OpenAI
from typing import List
from tracing import wrap_openai_client
from exceptions import OpenAIAPIError
from retry_utils import openai_retry

logger = logging.getLogger(__name__)

class VectorStore:
    def __init__(self, api_key: str, dimension: int = 1536):
        """
        Initialize VectorStore with OpenAI client and FAISS index.
        Default dimension 1536 is for text-embedding-3-small / text-embedding-ada-002.
        """
        self.client = wrap_openai_client(OpenAI(api_key=api_key))
        self.dimension = dimension
        self.index = faiss.IndexFlatL2(dimension)
        self.doc_map = {} # Maps internal ID to doc ID
        self.documents = [] # Metadata storage

    @openai_retry
    def embed_text(self, text: str) -> List[float]:
        """
        Generate embedding for a given text using OpenAI API.

        Raises:
            OpenAIAPIError: On transient OpenAI failures (retried automatically).
        """
        text = text.replace("\n", " ")
        try:
            response = self.client.embeddings.create(
                input=[text],
                model="text-embedding-3-small"
            )
            return response.data[0].embedding
        except Exception as e:
            raise OpenAIAPIError(f"Embedding generation failed: {e}") from e

    def add_document(self, doc_id: str, text: str):
        """
        Embeds text and adds it to the FAISS index.
        """
        if not text or not text.strip():
            logger.warning("Skipping empty text for doc %s", doc_id)
            return

        embedding = self.embed_text(text)
        vector = np.array([embedding], dtype='float32')

        self.index.add(vector)

        # Store metadata
        internal_id = self.index.ntotal - 1
        self.doc_map[internal_id] = doc_id
        self.documents.append({"id": doc_id, "text_preview": text[:50]})

    def save_index(self, output_dir: str):
        """
        Saves the FAISS index and metadata to disk.
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        index_path = os.path.join(output_dir, "vectors.index")
        faiss.write_index(self.index, index_path)

        meta_path = os.path.join(output_dir, "vectors_meta.pkl")
        with open(meta_path, "wb") as f:
            pickle.dump({"doc_map": self.doc_map, "documents": self.documents}, f)

        logger.info("Saved vector index to %s with %d vectors.", index_path, self.index.ntotal)

    def load_index(self, input_dir: str):
        """
        Loads the FAISS index and metadata from disk.
        """
        index_path = os.path.join(input_dir, "vectors.index")
        meta_path = os.path.join(input_dir, "vectors_meta.pkl")

        if os.path.exists(index_path) and os.path.exists(meta_path):
            self.index = faiss.read_index(index_path)
            with open(meta_path, "rb") as f:
                data = pickle.load(f)
                self.doc_map = data["doc_map"]
                self.documents = data["documents"]
            logger.info("Loaded vector index from %s with %d vectors.", input_dir, self.index.ntotal)
        else:
            logger.warning("Index not found in %s, starting fresh.", input_dir)

    def search(self, query: str, k: int = 3) -> List[dict]:
        """
        Searches the index for the query text.
        """
        if self.index.ntotal == 0:
            return []

        embedding = self.embed_text(query)
        vector = np.array([embedding], dtype='float32')

        distances, indices = self.index.search(vector, k)

        results = []
        for idx in indices[0]:
            if idx != -1 and idx in self.doc_map:
                doc_id = self.doc_map[idx]
                doc_meta = next((d for d in self.documents if d["id"] == doc_id), {"text_preview": "N/A"})
                results.append({"id": doc_id, "text": doc_meta.get("text_preview", "")})

        return results
