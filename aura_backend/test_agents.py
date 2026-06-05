import unittest
import asyncio
from services.agent import agent_orchestrator, DEFAULT_INSTRUCTIONS
from services.memory import memory_service
from config import settings

class TestMultiAgentIntelligence(unittest.TestCase):
    def setUp(self):
        # Ensure a clean state
        settings.gemini_api_key = "" # Run in mock mode for unit tests
        settings.debug_mode = True   # Enable debug mode to test local fallbacks
        agent_orchestrator.client = None
        memory_service.client_ai = None

    def test_intent_classification_fallback(self):
        """Verify fallback classification works in mock mode (no API key)."""
        intent = agent_orchestrator.classify_intent("Create a task for studies")
        self.assertEqual(intent, "task_planner")

    def test_memory_extraction_fallback(self):
        """Verify memory extraction runs locally and extracts facts without calling Gemini."""
        facts = memory_service.extract_memories([
            {"role": "user", "content": "I am studying CS"},
            {"role": "assistant", "content": "That is neat!"}
        ])
        self.assertEqual(facts, ["The user is studying CS."])

    def test_agent_orchestration_mock_routing(self):
        """Verify that streaming mock responses classify intents and output tool actions."""
        async def run_stream():
            chunks = []
            async for chunk in agent_orchestrator.execute_agent_stream(
                agent_id="orchestrator",
                messages=[{"role": "user", "content": "remind me to study task planning"}]
            ):
                chunks.append(chunk)
            return "".join(chunks)

        response = asyncio.run(run_stream())
        
        self.assertIn("[Mock Mode - Task Planner]", response)
        self.assertIn("[ACTIONS]", response)
        self.assertIn("add_task", response)
        self.assertIn("Study Session", response)

    def test_finance_advisor_mock_routing(self):
        """Verify that finance advisor agent produces correct financial actions in mock mode."""
        async def run_stream():
            chunks = []
            async for chunk in agent_orchestrator.execute_agent_stream(
                agent_id="finance_advisor",
                messages=[{"role": "user", "content": "I spent 15 dollars on food"}]
            ):
                chunks.append(chunk)
            return "".join(chunks)

        response = asyncio.run(run_stream())
        
        self.assertIn("[Mock Mode - Finance Advisor]", response)
        self.assertIn("add_expense", response)
        self.assertIn("15.0", response)

if __name__ == "__main__":
    unittest.main()
