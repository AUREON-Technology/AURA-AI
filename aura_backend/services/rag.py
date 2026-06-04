import logging
from typing import List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
from google import genai
from google.genai import types
from config import settings

logger = logging.getLogger(__name__)

class RAGService:
  def __init__(self):
    self.collection_name = "aura_knowledge"
    self.client = None
    self._is_local = False

    # Try connecting to Qdrant Cloud
    if settings.qdrant_host:
      try:
        self.client = QdrantClient(
          url=settings.qdrant_host,
          api_key=settings.qdrant_api_key or None,
        )
        # Verify connectivity
        self.client.get_collections()
        logger.info("Connected to Qdrant Cloud successfully.")
      except Exception as e:
        logger.error(f"Failed to connect to Qdrant Cloud: {e}. Falling back to local disk storage.")
        self.client = None

    if not self.client:
      # Fallback to offline local Qdrant (disk-backed via sqlite in-process)
      try:
        self.client = QdrantClient(path="local_qdrant_storage")
        self.collection_name = "aura_knowledge_local"
        self._is_local = True
        logger.info("Connected to local offline Qdrant client (SQLite-backed).")
      except Exception as local_err:
        logger.error(f"Failed to initialize local Qdrant client: {local_err}")
        self.client = None

    self.client_ai = None
    # Configure Gemini SDK
    if settings.gemini_api_key:
      self.client_ai = genai.Client(api_key=settings.gemini_api_key)

    # Initialize collection and indexes at startup
    self._ensure_collection_exists(384 if self._is_local else 768)

  def _ensure_collection_exists(self, vector_size: int):
    if not self.client:
      return
    try:
      collections = self.client.get_collections()
      existing = [c.name for c in collections.collections]
      if self.collection_name not in existing:
        logger.info(f"Creating Qdrant collection: {self.collection_name} with {vector_size} dimensions")
        self.client.create_collection(
          collection_name=self.collection_name,
          vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        
      # Create payload indexes for fast filtered query access (runs on new or existing collections, safe fallback)
      try:
        from qdrant_client.http import models as q_models
        self.client.create_payload_index(
          collection_name=self.collection_name,
          field_name="type",
          field_schema=q_models.PayloadSchemaType.KEYWORD,
        )
        self.client.create_payload_index(
          collection_name=self.collection_name,
          field_name="user_id",
          field_schema=q_models.PayloadSchemaType.KEYWORD,
        )
      except Exception as idx_err:
        # Silently bypass if indexes already exist
        logger.debug(f"Payload index setup check completed: {idx_err}")
    except Exception as e:
      logger.error(f"Error checking/creating Qdrant collection: {e}")

  def _get_embedding(self, text: str) -> List[float]:
    """Generates embedding. Uses Gemini models/text-embedding-004 (768d) if online, otherwise falls back to local encoders."""
    # 1. Try Gemini text-embedding-004 first
    if self.client_ai:
      try:
        response = self.client_ai.models.embed_content(
          model="text-embedding-004",
          contents=text,
          config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT"
          )
        )
        return response.embeddings[0].values
      except Exception as e:
        logger.warning(f"Failed to generate embedding with Gemini: {e}. Trying local fallback.")

    # 2. Try Local sentence-transformers (lightweight MiniLM)
    try:
      if not hasattr(self, '_local_encoder'):
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading local sentence-transformers model '{settings.local_embedding_model}'...")
        self._local_encoder = SentenceTransformer(settings.local_embedding_model)
      
      embeddings = self._local_encoder.encode([text])
      return embeddings[0].tolist()
    except Exception as sent_err:
      logger.warning(f"Local sentence-transformers embedding failed: {sent_err}. Trying Ollama local embedding.")

    # 3. Try Ollama local model embedding fallback
    try:
      from services.ollama import ollama_service
      emb = ollama_service.generate_embeddings(text)
      if emb:
        return emb
    except Exception as ollama_err:
      logger.warning(f"Ollama local embedding failed: {ollama_err}")

    # 4. Final mock zero-vector fallback (default 384 for sentence-transformers, 768 for Gemini)
    default_dim = 384 if self._is_local else 768
    return [0.0] * default_dim

  def ingest_document(self, doc_id: str, content: str, metadata: Dict[str, Any]) -> bool:
    """Chunks and uploads a document to local/cloud Qdrant."""
    if not self.client:
      logger.warning("Cannot ingest document: Qdrant client not initialized.")
      return False

    try:
      chunks = self._chunk_text(content, max_chars=1000, overlap=100)
      points = []

      # Generate first embedding to determine dimension dynamically and ensure collection
      if chunks:
        first_emb = self._get_embedding(chunks[0])
        self._ensure_collection_exists(len(first_emb))

        for idx, chunk in enumerate(chunks):
          # Skip embedding generation for first chunk since we already did it
          embedding = first_emb if idx == 0 else self._get_embedding(chunk)
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
      return False
    except Exception as e:
      logger.error(f"Failed to ingest document {doc_id}: {e}")
      return False

  def query_knowledge(self, query: str, limit: int = 4, user_id: str = None) -> List[Dict[str, Any]]:
    """Queries Qdrant local/cloud for relevant text chunks."""
    if not self.client:
      logger.warning("Cannot query Qdrant: Client not initialized.")
      return []

    try:
      query_vector = self._get_embedding(query)
      self._ensure_collection_exists(len(query_vector))
      
      # Security partition: filter documents by user_id
      query_filter = None
      if user_id:
        from qdrant_client.http import models as q_models
        query_filter = q_models.Filter(
          should=[
            q_models.FieldCondition(
              key="user_id",
              match=q_models.MatchValue(value=user_id)
            ),
            q_models.FieldCondition(
              key="user_id",
              match=q_models.MatchValue(value="global")
            )
          ]
        )
      
      search_results = self.client.query_points(
        collection_name=self.collection_name,
        query=query_vector,
        query_filter=query_filter,
        limit=limit
      )
      
      results = []
      for result in search_results.points:
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
