import numpy as np
import logging
import os
import json
from services.rag import rag_service

logger = logging.getLogger(__name__)

AGENT_REPRESENTATIVES = {
    "chat_agent": [
        "hello",
        "hi there",
        "how are you today",
        "good morning",
        "nice to meet you",
        "let's chat",
        "who are you",
        "what is your name",
        "tell me a joke",
        "how is the weather"
    ],
    "memory_agent": [
        "remember that I like coffee",
        "forget my previous address",
        "what is my wife's name",
        "recall my favorite color",
        "what do you know about me",
        "list my memories",
        "save a fact about me",
        "add a fact about my preferences"
    ],
    "rag_agent": [
        "what does the policy say about annual leave",
        "summarize the quarterly report PDF",
        "extract key points from the document",
        "search the company manual",
        "tell me about the database schema from the file",
        "query the knowledge base",
        "what information is in the file",
        "find notes in my documents"
    ],
    "automation_agent": [
        "send an email to John",
        "check my outlook inbox",
        "schedule a meeting for tomorrow at 3pm",
        "create a github issue for the bug",
        "download the spreadsheet from my drive",
        "sync my calendar events",
        "analyze this email"
    ],
    "knowledge_agent": [
        "ingest this document",
        "index the new text file",
        "add this text to the knowledge base",
        "upload document to Qdrant",
        "update the vector DB collection",
        "process this text file"
    ],
    "coding_agent": [
        "write a python function to parse JSON",
        "debug this dart code",
        "how do I implement a binary search tree",
        "explain clean architecture in flutter",
        "give me a SQL query to select all users",
        "refactor this code snippet",
        "write a bash script",
        "how does inheritance work in Java"
    ],
    "planning_agent": [
        "add a high priority task to my todo list",
        "show my calendar events",
        "create a shopping checklist",
        "help me decide between buying a Mac or a PC",
        "evaluate the cost benefit of this plan",
        "organize my schedule for next week",
        "prioritize my tasks",
        "risk assessment",
        "cost-benefit analysis"
    ]
}

class AgentRouter:
    def __init__(self):
        self._centroids = {}
        self._initialized = False

    def _initialize_centroids(self):
        if self._initialized:
            return

        centroid_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "routing_centroids.json")
        if os.path.exists(centroid_file):
            try:
                with open(centroid_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._centroids = {k: np.array(v) for k, v in data.items()}
                self._initialized = True
                logger.info("Agent router centroids loaded successfully from routing_centroids.json.")
                return
            except Exception as e:
                logger.error(f"Failed to load routing_centroids.json: {e}. Falling back to live calculation.")

        logger.info("Initializing Agent router centroids live...")
        for agent_id, phrases in AGENT_REPRESENTATIVES.items():
            embeddings = []
            for phrase in phrases:
                emb = rag_service._get_embedding(phrase)
                embeddings.append(emb)
            # Calculate the centroid vector
            self._centroids[agent_id] = np.mean(embeddings, axis=0)

        # Cache to disk for next boot
        try:
            with open(centroid_file, "w", encoding="utf-8") as f:
                json.dump({k: v.tolist() for k, v in self._centroids.items()}, f, indent=2)
            logger.info("Saved computed agent routing centroids to cache file.")
        except Exception as cache_err:
            logger.error(f"Failed to save computed agent centroids cache: {cache_err}")

        self._initialized = True
        logger.info("Agent router centroids initialized successfully.")

    def _cosine_similarity(self, a, b) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return np.dot(a, b) / (norm_a * norm_b)

    def route_intent(self, text: str) -> str:
        """Routes the user text message to the closest agent centroid using embedding cosine similarities."""
        try:
            self._initialize_centroids()
            text_vector = np.array(rag_service._get_embedding(text))

            best_agent = "chat_agent"
            best_score = -1.0

            for agent_id, centroid in self._centroids.items():
                score = self._cosine_similarity(text_vector, centroid)
                if score > best_score:
                    best_score = score
                    best_agent = agent_id

            logger.info(f"Routed text intent to agent: '{best_agent}' (Score: {best_score:.2f})")
            return best_agent
        except Exception as e:
            logger.error(f"Agent routing failed: {e}")
            return "chat_agent"

    def evaluate_router(self) -> dict:
        """Evaluates router accuracy against a validation dataset and prints metrics."""
        validation_data = [
            # Chat
            ("Hey there!", "chat_agent"),
            ("Can you tell me a funny joke?", "chat_agent"),
            ("How's the weather like?", "chat_agent"),
            # Memory
            ("Please remember that my favorite food is pizza", "memory_agent"),
            ("Forget my phone number", "memory_agent"),
            ("What is my dog's name?", "memory_agent"),
            # RAG
            ("Search my documents for vacation policies", "rag_agent"),
            ("Extract summary from the handbook PDF", "rag_agent"),
            ("What is the company policy on code reviews?", "rag_agent"),
            # Automation
            ("Send a quick email to boss@company.com", "automation_agent"),
            ("Create a GitHub issue for login button crashes", "automation_agent"),
            ("Add calendar event tomorrow at 10 AM", "automation_agent"),
            # Knowledge
            ("Upload this text to my vector database", "knowledge_agent"),
            ("Ingest document files into the store", "knowledge_agent"),
            # Coding
            ("Write a rust function for binary search", "coding_agent"),
            ("How to fix null pointer exception in java?", "coding_agent"),
            # Planning
            ("Prioritize my tasks for today", "planning_agent"),
            ("Create a shopping list checklist", "planning_agent"),
            ("What are the pros and cons of moving to Chicago?", "planning_agent")
        ]

        correct = 0
        total = len(validation_data)
        results = []

        for text, true_agent in validation_data:
            pred_agent = self.route_intent(text)
            is_correct = (pred_agent == true_agent)
            if is_correct:
                correct += 1
            results.append({
                "text": text,
                "expected": true_agent,
                "predicted": pred_agent,
                "correct": is_correct
            })

        accuracy = correct / total
        metrics = {
            "total_examples": total,
            "correct_predictions": correct,
            "accuracy": accuracy,
            "details": results
        }
        logger.info(f"Agent Router Evaluation completed: Accuracy = {accuracy * 100:.2f}%")
        return metrics

agent_router = AgentRouter()
