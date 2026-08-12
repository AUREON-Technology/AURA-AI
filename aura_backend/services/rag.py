import logging
from typing import List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct
from config import settings

logger = logging.getLogger(__name__)


class EmbeddingUnavailableError(RuntimeError):
    """No local embedding backend could produce a vector.

    A distinct type so callers can tell "the inference host is down" from a bug. Raised instead of
    returning a zero vector, which Qdrant would happily index and cosine distance could not rank.
    """


class EmbeddingDimensionMismatchError(RuntimeError):
    """A query embedded to a different width than the collection stores.

    Distinct from EmbeddingUnavailableError because the remedy is different: this one means the
    embedding backend changed under a populated index and someone must reindex on purpose.
    """


class RAGService:
  def __init__(self):
    self.collection_name = "aura_knowledge"
    self.client = None
    self._is_local = False
    self._embedding_cache = {}
    self._verified_collections = set()

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

    # No eager collection creation. The dimension is a property of whichever local backend
    # answers, and this line used to guess it (768 with a key present, else 384). A wrong guess is
    # not harmless: the first real embedding then hits the dimension-mismatch branch below and
    # *deletes the collection*. `ingest_document` and `query_knowledge` each call
    # `_ensure_collection_exists` with a real vector's length, so the guess bought nothing.

  def _ensure_collection_exists(self, vector_size: int, allow_recreate: bool = True):
    if not self.client:
      return
    if self.collection_name in self._verified_collections:
      return
    try:
      collections = self.client.get_collections()
      existing = [c.name for c in collections.collections]
      if self.collection_name in existing:
        try:
          coll_info = self.client.get_collection(self.collection_name)
          current_size = None
          if hasattr(coll_info.config.params.vectors, 'size'):
            current_size = coll_info.config.params.vectors.size
          elif isinstance(coll_info.config.params.vectors, dict) and 'size' in coll_info.config.params.vectors:
            current_size = coll_info.config.params.vectors['size']
            
          if current_size and current_size != vector_size:
            if not allow_recreate:
              # Reached from a *read*. Recreating here would mean a search silently destroys the
              # corpus it was asked to search -- the caller gets an empty result and the data is
              # gone. A mismatch on read means the embedding backend changed under a populated
              # index, which needs a deliberate reindex, not a side effect of a query.
              raise EmbeddingDimensionMismatchError(
                f"Collection {self.collection_name} stores {current_size}-dim vectors but the "
                f"query embedded to {vector_size} dims. The embedding backend changed; reindex "
                f"deliberately rather than letting a query drop the collection."
              )
            logger.warning(f"Dimension mismatch for collection {self.collection_name}: expected {vector_size}, found {current_size}. Recreating collection...")
            self.client.delete_collection(self.collection_name)
            existing.remove(self.collection_name)
        except Exception as check_err:
          logger.error(f"Error checking Qdrant collection dimensions: {check_err}")

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
      
      self._verified_collections.add(self.collection_name)
    except Exception as e:
      logger.error(f"Error checking/creating Qdrant collection: {e}")

  def _get_embedding(self, text: str) -> List[float]:
    """Embeds text on our own infrastructure. Raises when it cannot.

    **Ollama first, sentence-transformers second.** That ordering is about memory, not quality:
    Ollama already holds the model in its own container, so embedding there costs the API process
    nothing, while `SentenceTransformer(...)` loads a second model into *this* process. On a 4 GB
    box next to a 7B chat model that is the difference between working and OOM -- which is why the
    sentence-transformers path had been restricted to debug mode. Now that it is a fallback rather
    than the primary, the restriction is no longer needed to avoid it running by default.

    **Never returns a zero vector.** The previous final fallback synthesised `[0.0] * dim`, which
    Qdrant accepts and cosine distance cannot rank -- so an embedding outage silently filled the
    index with points that match everything equally and retrieval returned confident nonsense.
    Failing loudly here means ingestion stops and someone fixes the inference host, which is the
    behaviour the brief asks for: never fabricate a result because a subsystem failed.

    Callers must handle EmbeddingUnavailableError. `ingest_document` and the search paths below
    do; both report the failure rather than proceeding with a degraded index.
    """
    import hashlib
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if text_hash in self._embedding_cache:
      logger.info("Embedding cache hit.")
      return self._embedding_cache[text_hash]

    res_embedding = None
    failures = []

    # 1. Ollama, in its own container.
    try:
      from services.ollama import ollama_service
      emb = ollama_service.generate_embeddings(text)
      if emb:
        res_embedding = emb
      else:
        failures.append("ollama returned an empty vector")
    except Exception as ollama_err:
      failures.append(f"ollama: {ollama_err}")

    # 2. In-process sentence-transformers. Costs this process the model's resident memory, so it
    #    runs only when Ollama could not answer.
    if not res_embedding:
      try:
        if not hasattr(self, '_local_encoder'):
          from sentence_transformers import SentenceTransformer
          logger.info(f"Loading local sentence-transformers model '{settings.local_embedding_model}'...")
          self._local_encoder = SentenceTransformer(settings.local_embedding_model)

        embeddings = self._local_encoder.encode([text])
        res_embedding = embeddings[0].tolist()
      except Exception as sent_err:
        failures.append(f"sentence-transformers: {sent_err}")

    if not res_embedding:
      raise EmbeddingUnavailableError(
        "No local embedding backend could produce a vector: " + "; ".join(failures)
      )

    # The two backends have different dimensions, and `_ensure_collection_exists` *recreates* the
    # collection on a dimension change -- meaning a silent switch between them wipes the index.
    # Recording which one answered makes that visible in the log before it happens.
    logger.info(f"Embedded {len(text)} chars into {len(res_embedding)} dimensions.")

    self._embedding_cache[text_hash] = res_embedding
    return res_embedding

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
          
          from services.deterministic_id import get_deterministic_id
          points.append(PointStruct(
            id=get_deterministic_id(point_id),
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

  def ingest_pdf(self, doc_id: str, pdf_bytes: bytes, metadata: Dict[str, Any]) -> bool:
    """Extracts text from PDF bytes and ingests it into Qdrant."""
    try:
      import io
      from pypdf import PdfReader
      
      reader = PdfReader(io.BytesIO(pdf_bytes))
      text = ""
      for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
          text += page_text + "\n"
          
      if not text.strip():
        logger.warning(f"No text extracted from PDF {doc_id}.")
        return False
        
      return self.ingest_document(doc_id=doc_id, content=text, metadata=metadata)
    except Exception as e:
      logger.error(f"Failed to parse and ingest PDF {doc_id}: {e}")
      return False

  def query_knowledge(self, query: str, limit: int = 4, user_id: str = None) -> List[Dict[str, Any]]:
    """Queries Qdrant local/cloud for relevant text chunks."""
    if not self.client:
      logger.warning("Cannot query Qdrant: Client not initialized.")
      return []

    try:
      query_vector = self._get_embedding(query)
      # allow_recreate=False: a read must never drop the collection. See the mismatch branch in
      # _ensure_collection_exists.
      self._ensure_collection_exists(len(query_vector), allow_recreate=False)
      
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
