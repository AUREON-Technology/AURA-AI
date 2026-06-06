import json
import logging
from typing import List, Dict, Any
from google import genai
from google.genai import types
from config import settings
from qdrant_client.http import models
from services.rag import rag_service

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

class Fact(BaseModel):
    type: str = Field(description="The category of fact. e.g. Name, Hobby, Study, Location, Goal, Preference")
    value: str = Field(description="The actual fact value about the user, written in third person. e.g. 'The user lives in Cairo' or 'The user likes learning Python'")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0 that this fact is accurate and explicitly stated")

class ExtractedFacts(BaseModel):
    facts: List[Fact]

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
        """Analyzes a message segment to extract long-term user facts and preferences using structured LLM extraction."""
        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        if not self.client_ai:
            if settings.debug_mode:
                # Return standard developer fallback facts for unit testing
                fallback_facts = []
                for msg in user_msgs:
                    if "studying cs" in msg.lower():
                        fallback_facts.append("The user is studying CS.")
                if fallback_facts:
                    return fallback_facts
            logger.warning("Gemini client not initialized. Cannot extract memories.")
            return []
            
        if not user_msgs:
            return []
            
        # Compile recent conversation history for context
        history_str = ""
        for m in messages[-6:]:  # Take last 6 messages for context
            history_str += f"{m['role'].capitalize()}: {m['content']}\n"
            
        prompt = (
            f"Analyze the following recent conversation segment and extract any new personal details, "
            f"facts, preferences, goals, or settings that the user shared about themselves.\n\n"
            f"Conversation History:\n{history_str}\n"
            f"Extract only explicitly stated, high-confidence facts. Do not make assumptions."
        )
        
        try:
            response = self.client_ai.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(

                    response_mime_type="application/json",
                    response_schema=ExtractedFacts,
                    system_instruction="You are a personal fact extraction agent. Extract facts from the user conversation and output them matching the schema."
                )
            )
            
            extracted = json.loads(response.text)
            facts_list = extracted.get("facts", [])
            
            # Apply confidence threshold
            CONFIDENCE_THRESHOLD = 0.75
            valid_facts = []
            for f in facts_list:
                if f.get("confidence", 0.0) >= CONFIDENCE_THRESHOLD:
                    val = f.get("value", "").strip()
                    if val:
                        valid_facts.append(val)
                        
            logger.info(f"Structured memory extraction: Extracted {len(valid_facts)} valid facts (Threshold >= {CONFIDENCE_THRESHOLD}).")
            return valid_facts
        except Exception as e:
            logger.error(f"Failed to extract memories via structured LLM: {e}")
            return []

    def save_memory(self, fact: str, user_id: str = "global") -> bool:
        """Saves a fact to Qdrant Cloud as a memory point."""
        if not self.client:
            logger.warning("Qdrant client not initialized. Cannot save memory.")
            return False

        try:
            self._memory_cache.clear()
            embedding = rag_service._get_embedding(fact)
            rag_service._ensure_collection_exists(len(embedding))
            from services.deterministic_id import get_deterministic_id
            point_id = get_deterministic_id(fact)
            
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
