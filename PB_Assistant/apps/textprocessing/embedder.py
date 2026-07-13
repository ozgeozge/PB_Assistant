import numpy as np
import logging
from typing import List, Tuple
from django.db import transaction, IntegrityError
from sentence_transformers import SentenceTransformer
from langchain_classic.text_splitter import RecursiveCharacterTextSplitter
from PB_Assistant.models import AcademicPaperText, AcademicPaperTextEmbedding

logger = logging.getLogger(__name__)

class TextEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5", chunk_size: int = 800,
                 chunk_overlap: int = 100):
        self.model = SentenceTransformer(model_name)
        self.splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    def _chunk(self, text: str) -> List[str]:
        return self.splitter.split_text(text)

    def embed_academic_paper(self, paper_text: AcademicPaperText) -> bool:
        try:
            chunks = self._chunk(paper_text.text)
            vectors = self.model.encode(chunks, convert_to_numpy=True, show_progress_bar=False)
            norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-12
            vectors = vectors / norms
            embeddings = [
                AcademicPaperTextEmbedding(
                    academicpaper_text=paper_text,
                    chunk_index=i,
                    content=chunk,
                    vector=vec.tolist(),
                )
                for i, (chunk, vec) in enumerate(zip(chunks, vectors))
            ]
            with transaction.atomic():
                AcademicPaperTextEmbedding.objects.bulk_create(
                    embeddings,
                    update_conflicts=True,
                    update_fields=["content", "vector"],
                    unique_fields=["academicpaper_text", "chunk_index"],
                )
            return True
        except IntegrityError as e:
            logger.warning(f"DB error for academic paper {paper_text.academicpaper_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in embed_academic_paper: {e}", exc_info=True)
        return False

    def embed_text(self, text: str) -> List[float]:
        vectors = self.model.encode(text, convert_to_numpy=True, show_progress_bar=False).tolist()
        return vectors
