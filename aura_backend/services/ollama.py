import json
import logging
from typing import List, Dict, Any, AsyncGenerator
import httpx
from config import settings

logger = logging.getLogger(__name__)

class OllamaService:
    def __init__(self):
        self.host = settings.ollama_host.rstrip('/')
        self.model = settings.ollama_model

    async def check_health(self) -> bool:
        """Pings local Ollama endpoint to verify if it is running and active."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.host}/api/tags", timeout=2.0)
                if response.status_code == 200:
                    data = response.json()
                    models = [m.get("name") for m in data.get("models", [])]
                    logger.info(f"Ollama is running. Available models: {models}")
                    # Log if the configured model is pulled
                    matched = any(self.model in m or m in self.model for m in models)
                    if not matched:
                        logger.warning(f"Configured Ollama model '{self.model}' is not in local list. Inference may auto-trigger a download or fail.")
                    return True
                return False
        except Exception as e:
            logger.warning(f"Ollama health check failed: {e}. Ollama is likely not running locally.")
            return False

    async def generate_chat_stream(
        self,
        messages: List[Dict[str, str]],
        system_instruction: str = None
    ) -> AsyncGenerator[str, None]:
        """Streams chat completion responses from local Ollama instance."""
        payload_messages = []
        
        # Inject system instruction if provided
        if system_instruction:
            payload_messages.append({
                "role": "system",
                "content": system_instruction
            })

        for msg in messages:
            # Map role names if needed (Ollama supports system, user, assistant)
            role = msg["role"]
            if role == "model":
                role = "assistant"
            payload_messages.append({
                "role": role,
                "content": msg["content"]
            })

        payload = {
            "model": self.model,
            "messages": payload_messages,
            "stream": True,
            "options": {
                "temperature": 0.7
            }
        }

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{self.host}/api/chat",
                    json=payload,
                    timeout=30.0
                ) as response:
                    if response.status_code != 200:
                        yield f"\n[Ollama Error: Server returned status {response.status_code}]"
                        return

                    async for line in response.iter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk_data = json.loads(line)
                            content = chunk_data.get("message", {}).get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f"Error communicating with local Ollama: {e}")
            yield f"\n[Offline Fallback Error: Failed to generate response from Ollama: {e}]"

    def generate_embeddings(self, text: str) -> List[float]:
        """Generates embeddings synchronously using local Ollama model."""
        # Try new /api/embed endpoint first, then old /api/embeddings
        try:
            with httpx.Client() as client:
                # Try new endpoint
                response = client.post(
                    f"{self.host}/api/embed",
                    json={"model": self.model, "input": text},
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    embeddings = data.get("embeddings", [])
                    if embeddings:
                        return embeddings[0]
                
                # Fallback to old endpoint
                response = client.post(
                    f"{self.host}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("embedding", [])
        except Exception as e:
            logger.error(f"Ollama local embedding generation failed: {e}")
        return []

ollama_service = OllamaService()
