import os
from dotenv import load_dotenv

load_dotenv()

DB_DRIVER = os.getenv("DB_DRIVER", "")
DB_SERVER = os.getenv("DB_SERVER", "")
DB_DATABASE = os.getenv("DB_DATABASE", "")
DB_UID = os.getenv("DB_UID") or None
DB_PASSWORD = os.getenv("DB_PASSWORD") or None
ATTACHMENT_ROOT = os.getenv("ATTACHMENT_ROOT", "attachments").strip()
INDEX_SCHEDULER_ENABLED = os.getenv("INDEX_SCHEDULER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
INDEX_SCHEDULER_HOUR = int(os.getenv("INDEX_SCHEDULER_HOUR", "2"))
INDEX_SCHEDULER_MINUTE = int(os.getenv("INDEX_SCHEDULER_MINUTE", "0"))
INDEX_SCHEDULER_TIMEZONE = os.getenv("INDEX_SCHEDULER_TIMEZONE", "Asia/Makassar").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
GROQ_MODEL_120B = os.getenv("GROQ_MODEL_120B", "openai/gpt-oss-120b").strip()
GROQ_MODEL_QWEN_27B = os.getenv("GROQ_MODEL_QWEN_27B", "qwen/qwen3.8-27b").strip()
SIMILARITY_AI_MAX_CANDIDATES = int(os.getenv("SIMILARITY_AI_MAX_CANDIDATES", "5"))
SIMILARITY_REVIEW_THRESHOLD = float(os.getenv("SIMILARITY_REVIEW_THRESHOLD", "0.70"))
SIMILARITY_SUBMIT_THRESHOLD = float(os.getenv("SIMILARITY_SUBMIT_THRESHOLD", "0.40"))
SIMILARITY_TOP_K = int(os.getenv("SIMILARITY_TOP_K", "5"))

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
JWT_ISSUER = os.getenv("JWT_ISSUER", "")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "")
