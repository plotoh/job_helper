import json
import logging
from app.config import settings

logger = logging.getLogger(__name__)

BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
ADMIN_ID = settings.TELEGRAM_ADMIN_ID

# logger.info(f"🔑 Bot token loaded: {BOT_TOKEN[:10]}...")
# logger.info(f"👑 Admin ID: {ADMIN_ID}")

raw = settings.TELEGRAM_USERS_IDS.strip()
logger.info(f"📋 Raw users config: {raw}")

if raw.startswith('[') and raw.endswith(']'):
    USERS_IDS = json.loads(raw)
    logger.info(f"✅ Users loaded from JSON: {USERS_IDS}")
else:
    USERS_IDS = [int(x.strip()) for x in raw.split(',') if x.strip()]
    logger.info(f"✅ Users loaded from CSV: {USERS_IDS}")

logger.info(f"📊 Total allowed users: {len(USERS_IDS)}")