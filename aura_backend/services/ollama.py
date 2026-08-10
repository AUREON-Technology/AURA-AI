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

    async def generate_agent_stream(
        self,
        messages: List[Dict[str, str]],
        system_instruction: str = None,
        tools: List[Dict[str, Any]] = None,
        tool_impls: Dict[str, Any] = None,
        max_tool_rounds: int = 4,
        timeout_s: float = 120.0,
    ) -> AsyncGenerator[str, None]:
        """Streams an agent turn with tool calling, entirely on local inference.

        Replaces the hosted API's native function calling. Ollama exposes the same shape -- a
        `tools` array in the request, `message.tool_calls` in the response, and a `tool` role for
        results -- so the loop structure carries over rather than being reinvented as prompt
        scraping.

        `max_tool_rounds` bounds the loop. The previous implementation had `while True` around the
        tool exchange with no round limit, so a model that kept requesting the same tool would spin
        until the request timed out, holding a generation slot the whole time. Four rounds is
        enough for the tool chains this agent actually performs (search, then ingest, then query)
        and turns a pathological case into a bounded one.

        Tool *results* are untrusted input: they carry Drive file contents and knowledge-base text,
        which is exactly the indirect prompt-injection surface. This method does not attempt to
        sanitise them -- it is the legacy path, and the v3 kernel is where the trust-channel
        fencing lives. Keeping that boundary explicit here rather than implying a protection that
        is not present.
        """
        payload_messages = []
        if system_instruction:
            payload_messages.append({"role": "system", "content": system_instruction})
        for msg in messages:
            role = msg["role"]
            if role == "model":
                role = "assistant"
            payload_messages.append({"role": role, "content": msg["content"]})

        tool_impls = tool_impls or {}

        for round_index in range(max_tool_rounds + 1):
            payload = {
                "model": self.model,
                "messages": payload_messages,
                "stream": True,
                "options": {"temperature": 0.7},
            }
            # Only advertise tools while rounds remain. On the final pass they are withheld so the
            # model is forced to answer from what it already has rather than requesting a call
            # that will never be executed -- which would otherwise end the turn with silence.
            if tools and round_index < max_tool_rounds:
                payload["tools"] = tools

            assistant_text = ""
            tool_calls: List[Dict[str, Any]] = []

            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST", f"{self.host}/api/chat", json=payload, timeout=timeout_s
                    ) as response:
                        if response.status_code != 200:
                            yield f"\n[Local inference error: server returned {response.status_code}]"
                            return
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                chunk = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            message = chunk.get("message", {})
                            content = message.get("content", "")
                            if content:
                                assistant_text += content
                                yield content
                            for call in message.get("tool_calls") or []:
                                tool_calls.append(call)
            except Exception as e:
                logger.error(f"Local agent stream failed: {e}")
                yield f"\n[Local inference unavailable: {e}]"
                return

            if not tool_calls:
                return

            # Record what the model said before the calls, so the next round has the full context.
            payload_messages.append(
                {"role": "assistant", "content": assistant_text, "tool_calls": tool_calls}
            )

            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name", "")
                args = function.get("arguments") or {}
                if isinstance(args, str):
                    # Some builds serialise arguments as a JSON string rather than an object.
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                yield f"\n*[AURA is running tool: {name.replace('_', ' ').title()}...]*\n"

                impl = tool_impls.get(name)
                if impl is None:
                    result = {"error": f"Tool '{name}' is not supported."}
                else:
                    try:
                        result = impl(**args)
                    except TypeError as e:
                        # Wrong arguments from the model is an expected local-model outcome, not a
                        # crash: report it back so the model can correct itself next round.
                        result = {"error": f"Tool '{name}' rejected those arguments: {e}"}
                    except Exception as e:
                        logger.error(f"Tool {name} failed: {e}")
                        result = {"error": f"Tool '{name}' failed: {e}"}

                payload_messages.append(
                    {"role": "tool", "content": json.dumps(result, default=str)}
                )

    def generate_json(
        self,
        prompt: str,
        system_instruction: str = None,
        timeout_s: float = 60.0,
    ) -> Any:
        """Single-shot structured generation against local Ollama, returning parsed JSON.

        Replaces the `generate_content(config=GenerateContentConfig(response_mime_type=
        "application/json"))` calls that previously went to a hosted API. Ollama's `format:
        "json"` constrains decoding to valid JSON, which is the same guarantee those calls relied
        on.

        Returns ``None`` rather than raising on any failure -- unreachable server, non-200,
        malformed JSON. Every caller already has a meaningful default for "analysis unavailable",
        and a local model producing unparseable output is an expected condition, not an
        exceptional one. Returning None keeps that a routine branch instead of an error path that
        each caller has to rediscover.

        Synchronous because all three callers are synchronous. The timeout is generous: local
        inference on CPU is far slower than a hosted API, and 5 seconds would fail a 7B model
        doing a genuine extraction.
        """
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            # Deterministic: these are extraction and classification calls, where sampling adds
            # variance without adding quality.
            "options": {"temperature": 0.0},
        }

        # Bound before the try: the envelope parse below can itself raise JSONDecodeError, and the
        # handler reports this length.
        content = ""
        try:
            with httpx.Client() as client:
                response = client.post(
                    f"{self.host}/api/chat", json=payload, timeout=timeout_s
                )
            if response.status_code != 200:
                logger.warning(
                    f"Ollama JSON generation returned status {response.status_code}; "
                    f"caller will use its default."
                )
                return None
            content = response.json().get("message", {}).get("content", "")
            if not content.strip():
                return None
            return json.loads(content)
        except json.JSONDecodeError as e:
            # `format: "json"` makes this rare but not impossible -- a truncated response is still
            # invalid. Log the length, never the content: this text is model output derived from
            # user data.
            logger.warning(f"Ollama returned unparseable JSON ({len(content)} chars): {e}")
            return None
        except Exception as e:
            logger.error(f"Ollama JSON generation failed: {e}")
            return None

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
