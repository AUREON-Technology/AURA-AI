import json
import logging
from typing import List, Dict, Any
from google import genai
from google.genai import types
from config import settings
from qdrant_client.http import models
from services.rag import rag_service

logger = logging.getLogger(__name__)

class MemoryService:
    def __init__(self):
        self.client_ai = None
        if settings.gemini_api_key:
            self.client_ai = genai.Client(api_key=settings.gemini_api_key)
        self._memory_cache = {}

    @property
    def collection_name(self) -> str:
        return rag_service.collection_name

    @property
    def client(self):
        return rag_service.client


    def extract_memories(self, messages: List[Dict[str, str]]) -> List[str]:
        """Analyzes a message segment to extract long-term user facts and preferences using deterministic local rules."""
        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        if not user_msgs:
            return []
        
        last_msg = user_msgs[-1].strip()
        facts = []
        import re
        
        # Pattern 1: My name is X
        m = re.search(r"\bmy name is ([a-zA-Z\s]+)", last_msg, re.IGNORECASE)
        if m:
            name = m.group(1).strip(". ")
            if len(name.split()) <= 3:
                facts.append(f"The user's name is {name}.")
                
        # Pattern 2: I am studying X / I study X
        m = re.search(r"\bi (?:am studying|study) ([a-zA-Z\s]+)", last_msg, re.IGNORECASE)
        if m:
            subject = m.group(1).strip(". ")
            facts.append(f"The user is studying {subject}.")
            
        # Pattern 3: I like X / I love X
        m = re.search(r"\bi (?:like|love|prefer) ([a-zA-Z\s]+)", last_msg, re.IGNORECASE)
        if m:
            preference = m.group(1).strip(". ")
            facts.append(f"The user likes {preference}.")
            
        # Pattern 4: I want to X / My goal is X
        m = re.search(r"\bi want to ([a-zA-Z\s]+)", last_msg, re.IGNORECASE)
        if m:
            goal = m.group(1).strip(". ")
            facts.append(f"The user wants to {goal}.")
            
        # Pattern 5: I am working on X
        m = re.search(r"\bi (?:am working on|'m working on) ([a-zA-Z0-9\s]+)", last_msg, re.IGNORECASE)
        if m:
            project = m.group(1).strip(". ")
            facts.append(f"The user is working on {project}.")
            
        # Pattern 6: I live in X
        m = re.search(r"\bi live in ([a-zA-Z\s]+)", last_msg, re.IGNORECASE)
        if m:
            location = m.group(1).strip(". ")
            facts.append(f"The user lives in {location}.")
            
        return facts

    def save_memory(self, fact: str, user_id: str = "global") -> bool:
        """Saves a fact to Qdrant Cloud as a memory point."""
        if not self.client:
            logger.warning("Qdrant client not initialized. Cannot save memory.")
            return False

        try:
            self._memory_cache.clear()
            embedding = rag_service._get_embedding(fact)
            rag_service._ensure_collection_exists(len(embedding))
            # Use hash of the fact string as a stable ID
            point_id = hash(fact) & 0xFFFFFFFFFFFFFFFF
            
            payload = {
                "content": fact,
                "type": "user_memory",
                "user_id": user_id
            }

            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    models.PointStruct(
                        id=point_id,
                        vector=embedding,
                        payload=payload
                    )
                ]
            )
            logger.info(f"Memory saved successfully for {user_id}: {fact}")
            return True
        except Exception as e:
            logger.error(f"Error saving memory fact: {e}")
            return False

    def retrieve_relevant_memories(self, query: str, limit: int = 5, user_id: str = None) -> List[Dict[str, Any]]:
        """Retrieves user memories semantically relevant to the user query."""
        if not self.client:
            return []

        import time
        cache_key = (query, limit, user_id)
        if cache_key in self._memory_cache:
            timestamp, cached_results = self._memory_cache[cache_key]
            if time.time() - timestamp < 15 * 60:
                logger.info(f"Memory cache hit for query: '{query}'")
                return cached_results

        try:
            query_vector = rag_service._get_embedding(query)
            
            # Filter for user_memory type and user_id
            must_conditions = [
                models.FieldCondition(
                    key="type",
                    match=models.MatchValue(value="user_memory")
                )
            ]
            if user_id:
                must_conditions.append(
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=user_id)
                    )
                )
            memory_filter = models.Filter(must=must_conditions)

            search_results = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                query_filter=memory_filter,
                limit=limit
            )

            results = []
            for result in search_results.points:
                results.append({
                    "id": result.id,
                    "content": result.payload.get("content", ""),
                    "score": result.score
                })
            self._memory_cache[cache_key] = (time.time(), results)
            return results
        except Exception as e:
            logger.error(f"Error retrieving memories: {e}")
            return []

    def list_all_memories(self, user_id: str = None) -> List[Dict[str, Any]]:
        """Retrieves all stored user memories from Qdrant Cloud."""
        if not self.client:
            return []

        try:
            # Filter for user_memory type and user_id
            must_conditions = [
                models.FieldCondition(
                    key="type",
                    match=models.MatchValue(value="user_memory")
                )
            ]
            if user_id:
                must_conditions.append(
                    models.FieldCondition(
                        key="user_id",
                        match=models.MatchValue(value=user_id)
                    )
                )
            memory_filter = models.Filter(must=must_conditions)

            scroll_result = self.client.scroll(
                collection_name=self.collection_name,
                scroll_filter=memory_filter,
                limit=100,
                with_payload=True,
                with_vectors=False
            )

            points, _ = scroll_result
            results = []
            for point in points:
                results.append({
                    "id": point.id,
                    "content": point.payload.get("content", ""),
                })
            return results
        except Exception as e:
            logger.error(f"Error listing memories: {e}")
            return []

    def delete_memory(self, point_id: int) -> bool:
        """Deletes a memory point from Qdrant Cloud."""
        if not self.client:
            return False

        try:
            self._memory_cache.clear()
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(
                    points=[point_id]
                )
            )
            logger.info(f"Memory point deleted: {point_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting memory point {point_id}: {e}")
            return False

memory_service = MemoryService()
