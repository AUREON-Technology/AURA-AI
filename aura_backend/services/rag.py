import logging
from typing import List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
import google.generativeai as genai
from config import settings

logger = logging.getLogger(__name__)

class RAGService:
  def __init__(self):
    self.collection_name = "aura_knowledge"
    self.client = None

    # Try connecting to Qdrant Cloud
    if settings.qdrant_host:
      try:
        self.client = QdrantClient(
          url=settings.qdrant_host,
          api_key=settings.qdrant_api_key or None,
        )
        self._ensure_collection_exists()
        logger.info("Connected to Qdrant Cloud successfully.")
      except Exception as e:
        logger.error(f"Failed to connect to Qdrant Cloud: {e}. Falling back to mock client.")
        self.client = None
    else:
      logger.warning("QDRANT_HOST not provided. RAG functionality will run in fallback/mock mode.")

    # Configure Gemini SDK
    if settings.gemini_api_key:
      genai.configure(api_key=settings.gemini_api_key)

  def _ensure_collection_exists(self):
    if not self.client:
      return
    try:
      collections = self.client.get_collections()
      existing = [c.name for c in collections.collections]
      if self.collection_name not in existing:
        logger.info(f"Creating Qdrant collection: {self.collection_name} with 768 dimensions")
        self.client.create_collection(
          collection_name=self.collection_name,
          vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )
    except Exception as e:
      logger.error(f"Error checking/creating Qdrant collection: {e}")

  def _get_embedding(self, text: str) -> List[float]:
    """Generates 768-dim embeddings using Gemini's text-embedding-004 model."""
    if not settings.gemini_api_key:
      # Return mock mock embedding of size 768
      return [0.0] * 768
    try:
      response = genai.embed_content(
        model="models/text-embedding-004",
        content=text,
        task_type="retrieval_document"
      )
      return response["embedding"]
    except Exception as e:
      logger.error(f"Error generating embedding: {e}")
      return [0.0] * 768

  def ingest_document(self, doc_id: str, content: str, metadata: Dict[str, Any]) -> bool:
    """Chunks and uploads a document to Qdrant Cloud."""
    if not self.client:
      logger.warning("Cannot ingest document: Qdrant client not initialized.")
      return False

    try:
      # Simple recursive chunking logic
      chunks = self._chunk_text(content, max_chars=1000, overlap=100)
      points = []

      for idx, chunk in enumerate(chunks):
        embedding = self._get_embedding(chunk)
        point_id = f"{doc_id}_{idx}"
        
        # Build payload
        payload = {
          "chunk_index": idx,
          "content": chunk,
          "doc_id": doc_id,
          **metadata
        }
        
        points.append(PointStruct(
          id=hash(point_id) & 0xFFFFFFFFFFFFFFFF, # Native 64-bit int hash ID
          vector=embedding,
          payload=payload
        ))

      self.client.upsert(
        collection_name=self.collection_name,
        points=points
      )
      logger.info(f"Ingested document {doc_id} successfully, uploaded {len(points)} chunks.")
      return True
    except Exception as e:
      logger.error(f"Failed to ingest document {doc_id}: {e}")
      return False

  def query_knowledge(self, query: str, limit: int = 4) -> List[Dict[str, Any]]:
    """Queries Qdrant for relevant text chunks."""
    if not self.client:
      logger.warning("Cannot query Qdrant: Client not initialized.")
      return []

    try:
      query_vector = self._get_embedding(query)
      search_results = self.client.search(
        collection_name=self.collection_name,
        query_vector=query_vector,
        limit=limit
      )
      
      results = []
      for result in search_results:
        results.append({
          "score": result.score,
          "content": result.payload.get("content", ""),
          "doc_id": result.payload.get("doc_id", ""),
          "metadata": result.payload
        })
      return results
    except Exception as e:
      logger.error(f"Failed querying knowledge base: {e}")
      return []

  def _chunk_text(self, text: str, max_chars: int = 1000, overlap: int = 100) -> List[str]:
    chunks = []
    start = 0
    while start < len(text):
      end = start + max_chars
      if end > len(text):
        end = len(text)
      chunks.append(text[start:end])
      start += max_chars - overlap
    return chunks

rag_service = RAGService()
