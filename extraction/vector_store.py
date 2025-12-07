import os
import faiss
import numpy as np
import pickle
from openai import OpenAI
from typing import List

class VectorStore:
    def __init__(self, api_key: str, dimension: int = 1536):
        """
        Initialize VectorStore with OpenAI client and FAISS index.
        Default dimension 1536 is for text-embedding-3-small / text-embedding-ada-002.
        """
        self.client = OpenAI(api_key=api_key)
        self.dimension = dimension
        self.index = faiss.IndexFlatL2(dimension)
        self.doc_map = {} # Maps internal ID to doc ID
        self.documents = [] # Metadata storage

    def embed_text(self, text: str) -> List[float]:
        """
        Generate embedding for a given text using OpenAI API.
        """
        text = text.replace("\n", " ")
        try:
            response = self.client.embeddings.create(
                input=[text],
                model="text-embedding-3-small"
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"Error generating embedding: {e}")
            return [0.0] * self.dimension

    def add_document(self, doc_id: str, text: str):
        """
        Embeds text and adds it to the FAISS index.
        """
        if not text or not text.strip():
            print(f"Skipping empty text for doc {doc_id}")
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
            
        print(f"Saved vector index to {index_path} with {self.index.ntotal} vectors.")
