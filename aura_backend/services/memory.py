import json
import logging
from typing import List, Dict, Any
import google.generativeai as genai
from config import settings
from qdrant_client.http import models
from services.rag import rag_service

logger = logging.getLogger(__name__)

class MemoryService:
    def __init__(self):
        pass

    @property
    def collection_name(self) -> str:
        return rag_service.collection_name

    @property
    def client(self):
        return rag_service.client


    def extract_memories(self, messages: List[Dict[str, str]]) -> List[str]:
        """Analyzes a message segment to extract long-term user facts and preferences."""
        if not settings.gemini_api_key:
            return []

        # Format conversation history
        history_str = ""
        for msg in messages:
            role = "User" if msg["role"] == "user" else "Assistant"
            history_str += f"{role}: {msg['content']}\n"

        prompt = f"""
        Review the following conversation between the User and the Assistant.
        Extract any new, significant, long-term personal facts, goals, or preferences about the User.
        Ignore transient conversation details, greetings, or short-term topics.
        Examples of valid facts to extract:
        - "The user's name is Anas."
        - "The user is studying computer engineering."
        - "The user likes to study in the morning."
        - "The user wants to sleep 8 hours a day."
        - "The user is working on a mobile app project."

        Respond ONLY with a JSON object containing a list of strings under the key "extracted_facts".
        If no long-term facts are mentioned, return an empty list.

        Conversation History:
        {history_str}
        """

        try:
            model = genai.GenerativeModel("gemini-1.5-flash")
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            data = json.loads(response.text)
            facts = data.get("extracted_facts", [])
            return [str(f) for f in facts]
        except Exception as e:
            logger.error(f"Error extracting memories: {e}")
            return []

    def save_memory(self, fact: str) -> bool:
        """Saves a fact to Qdrant Cloud as a memory point."""
        if not self.client:
            logger.warning("Qdrant client not initialized. Cannot save memory.")
            return False

        try:
            embedding = rag_service._get_embedding(fact)
            rag_service._ensure_collection_exists(len(embedding))
            # Use hash of the fact string as a stable ID
            point_id = hash(fact) & 0xFFFFFFFFFFFFFFFF
            
            payload = {
                "content": fact,
                "type": "user_memory"
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
            logger.info(f"Memory saved successfully: {fact}")
            return True
        except Exception as e:
            logger.error(f"Error saving memory fact: {e}")
            return False

    def retrieve_relevant_memories(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieves user memories semantically relevant to the user query."""
        if not self.client:
            return []

        try:
            query_vector = rag_service._get_embedding(query)
            
            # Filter for user_memory type
            memory_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="type",
                        match=models.MatchValue(value="user_memory")
                    )
                ]
            )

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
            return results
        except Exception as e:
            logger.error(f"Error retrieving memories: {e}")
            return []

    def list_all_memories(self) -> List[Dict[str, Any]]:
        """Retrieves all stored user memories from Qdrant Cloud."""
        if not self.client:
            return []

        try:
            # Filter for user_memory type
            memory_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="type",
                        match=models.MatchValue(value="user_memory")
                    )
                ]
            )

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
