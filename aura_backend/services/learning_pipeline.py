import os
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)
LOGS_FILE_PATH = "learning_logs.json"

class LearningPipeline:
    def __init__(self):
        self._ensure_logs_file_exists()

    def _ensure_logs_file_exists(self):
        if not os.path.exists(LOGS_FILE_PATH):
            with open(LOGS_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump([], f)

    def log_feedback_record(self, record: Dict[str, Any]) -> bool:
        """Appends a client learning/feedback log into the backend logs file."""
        try:
            self._ensure_logs_file_exists()
            with open(LOGS_FILE_PATH, "r+", encoding="utf-8") as f:
                data = json.load(f)
                
                # Deduplicate records by ID
                if any(r.get("id") == record.get("id") for r in data):
                    return True
                
                data.append(record)
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Failed to log learning feedback: {e}")
            return False

    def load_all_records(self) -> List[Dict[str, Any]]:
        self._ensure_logs_file_exists()
        try:
            with open(LOGS_FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load learning records: {e}")
            return []

    def build_lora_dataset(self, format_type: str = "alpaca") -> List[Dict[str, Any]]:
        """Cleans, validates, and filters records to compile a high-quality dataset.
        
        Filters:
        - Drops records with low rating (< 3) unless a correction is present.
        - Removes empty prompts or responses.
        """
        raw_records = self.load_all_records()
        valid_records = []

        for r in raw_records:
            prompt = r.get("prompt", "").strip()
            response = r.get("response", "").strip()
            rating = r.get("rating", 5)
            correction = r.get("correction", "").strip()

            if not prompt or not response:
                continue
            
            # Exclude bad data unless it has a manual user correction
            if rating < 3 and not correction:
                continue

            valid_records.append(r)

        formatted_dataset = []
        for r in valid_records:
            prompt = r.get("prompt")
            # Use correction if available, otherwise response
            target_output = r.get("correction") if r.get("correction") else r.get("response")
            
            if format_type == "alpaca":
                formatted_dataset.append({
                    "instruction": "You are AURA AI. Respond to the user's inquiry.",
                    "input": prompt,
                    "output": target_output
                })
            elif format_type == "chatml":
                formatted_dataset.append({
                    "messages": [
                        {"role": "system", "content": "You are AURA AI, a premium Hybrid AI Assistant."},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": target_output}
                    ]
                })
            else: # openai / standard conversations format
                formatted_dataset.append({
                    "prompt": prompt,
                    "completion": target_output
                })

        return formatted_dataset

    def evaluate_metrics(self) -> Dict[str, Any]:
        """Calculates evaluation stats regarding model training logs."""
        records = self.load_all_records()
        if not records:
            return {
                "total_records": 0,
                "average_rating": 0.0,
                "corrected_percentage": 0.0,
                "model_distributions": {}
            }

        total = len(records)
        sum_rating = 0
        corrected_count = 0
        model_counts = {}

        for r in records:
            sum_rating += r.get("rating", 5)
            if r.get("correction"):
                corrected_count += 1
            model = r.get("model", "unknown")
            model_counts[model] = model_counts.get(model, 0) + 1

        return {
            "total_records": total,
            "average_rating": round(sum_rating / total, 2),
            "corrected_percentage": round((corrected_count / total) * 100, 1),
            "model_distributions": model_counts
        }

learning_pipeline = LearningPipeline()
