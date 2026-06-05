import numpy as np
import logging
from services.rag import rag_service

logger = logging.getLogger(__name__)

# Representative phrases for 5 sentiment classes
SENTIMENT_REPRESENTATIVES = {
    "Positive": [
        "thank you so much",
        "this is great",
        "awesome",
        "excellent work",
        "i love this app",
        "that was very helpful",
        "great job",
        "perfect, thanks"
    ],
    "Negative": [
        "this is bad",
        "terrible output",
        "useless response",
        "does not work",
        "awful",
        "worst assistant ever",
        "disappointed"
    ],
    "Neutral": [
        "hello",
        "tell me a fact",
        "show my task lists",
        "what is the time",
        "how do i write a loop",
        "explain clean architecture",
        "open the configuration"
    ],
    "Frustrated": [
        "this app is so annoying",
        "keeps failing",
        "useless garbage",
        "i am tired of this",
        "fix this error now",
        "why is it so slow",
        "horrible experience"
    ],
    "Urgent": [
        "immediate attention required",
        "critical deadline",
        "urgent issue",
        "do this as soon as possible",
        "asap",
        "need help immediately",
        "emergency"
    ]
}

class SentimentAnalyzer:
    def __init__(self):
        self._centroids = {}
        self._initialized = False

    def _initialize_centroids(self):
        if self._initialized:
            return
        
        logger.info("Initializing sentiment classifier centroids...")
        for sentiment, phrases in SENTIMENT_REPRESENTATIVES.items():
            embeddings = []
            for phrase in phrases:
                emb = rag_service._get_embedding(phrase)
                embeddings.append(emb)
            # Calculate the centroid vector
            self._centroids[sentiment] = np.mean(embeddings, axis=0)
        
        self._initialized = True
        logger.info("Sentiment classifier centroids initialized successfully.")

    def _cosine_similarity(self, a, b) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return np.dot(a, b) / (norm_a * norm_b)

    def analyze_sentiment(self, text: str) -> str:
        """Classifies sentiment using sentence embeddings cosine similarities."""
        try:
            self._initialize_centroids()
            text_vector = np.array(rag_service._get_embedding(text))
            
            best_sentiment = "Neutral"
            best_score = -1.0
            
            for sentiment, centroid in self._centroids.items():
                score = self._cosine_similarity(text_vector, centroid)
                if score > best_score:
                    best_score = score
                    best_sentiment = sentiment
            
            logger.info(f"Classified text sentiment as: '{best_sentiment}' (Score: {best_score:.2f})")
            return best_sentiment
        except Exception as e:
            logger.error(f"Sentiment analysis failed: {e}")
            return "Neutral"

sentiment_analyzer = SentimentAnalyzer()
