import os
import json
import logging
import firebase_admin
from firebase_admin import credentials, firestore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("backfill_firestore")

def main():
    logger.info("Starting Firestore migration script...")

    # 1. Initialize Firebase Admin SDK
    cred = None
    cred_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH", "credentials.json")
    
    # Try multiple common paths for credentials.json
    paths_to_try = [
        cred_path,
        "credentials.json",
        "../credentials.json",
        "aura_backend/credentials.json",
        "E:/AURA AI/credentials.json",
        "E:/AURA AI/aura_backend/credentials.json"
    ]
    
    for path in paths_to_try:
        if path and os.path.exists(path):
            try:
                cred = credentials.Certificate(path)
                logger.info(f"Loaded credentials from: {path}")
                break
            except Exception as e:
                logger.warning(f"Failed to load credentials from {path}: {e}")

    # Fallback to loading from credentials environment variable if available
    firebase_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
    if not cred and firebase_json:
        try:
            info = json.loads(firebase_json)
            cred = credentials.Certificate(info)
            logger.info("Loaded credentials from FIREBASE_CREDENTIALS_JSON environment variable.")
        except Exception as e:
            logger.error(f"Failed to load credentials from FIREBASE_CREDENTIALS_JSON: {e}")

    if not cred:
        logger.error("Error: Could not locate credentials.json or valid FIREBASE_CREDENTIALS_JSON. Migration aborted.")
        return

    try:
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Connected to Firestore successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Firestore client: {e}")
        return

    # List of legacy collections to migrate
    # Tuple format: (legacy_collection_name, new_subcollection_name)
    collections_to_migrate = [
        ("tasks", "tasks"),
        ("calendar_events", "calendar"),
        ("finance_records", "finance"),
        ("health_records", "health"),
        ("user_memories", "memories"),
    ]

    total_migrated = 0
    total_skipped = 0

    for legacy_col, new_sub in collections_to_migrate:
        logger.info(f"Scanning legacy root collection: '{legacy_col}'...")
        try:
            docs = list(db.collection(legacy_col).stream())
            logger.info(f"Found {len(docs)} documents in '{legacy_col}'.")
        except Exception as e:
            logger.error(f"Failed to read legacy collection '{legacy_col}': {e}. Skipping...")
            continue
        
        for doc in docs:
            doc_data = doc.to_dict()
            doc_id = doc.id
            
            # Extract owner ID from common identifier fields
            user_id = doc_data.get("userId") or doc_data.get("user_id") or doc_data.get("uid")
            
            if not user_id:
                logger.warning(f"Skipping document '{doc_id}' in '{legacy_col}': No owner identifier (userId/user_id/uid) found. Data keys: {list(doc_data.keys())}")
                total_skipped += 1
                continue
            
            # Destination path: /users/{userId}/{new_sub}/{doc_id}
            target_ref = db.collection("users").document(user_id).collection(new_sub).document(doc_id)
            
            try:
                # Copy to nested subcollection
                target_ref.set(doc_data)
                
                # Delete the legacy root collection document
                db.collection(legacy_col).document(doc_id).delete()
                
                logger.info(f"Migrated document '{doc_id}' from '{legacy_col}' to '/users/{user_id}/{new_sub}/{doc_id}'")
                total_migrated += 1
            except Exception as e:
                logger.error(f"Failed to migrate document '{doc_id}' in '{legacy_col}': {e}")
                total_skipped += 1

    logger.info(f"Migration completed. Total migrated: {total_migrated}, Total skipped/failed: {total_skipped}")

if __name__ == "__main__":
    main()
