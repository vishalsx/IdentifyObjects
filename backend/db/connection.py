import os
import motor.motor_asyncio
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DBNAME = os.getenv("MONGODB_DBNAME", "alphatubplay")

client = motor.motor_asyncio.AsyncIOMotorClient(MONGODB_URI)
db = client[MONGODB_DBNAME]

objects_collection = db["objects"]
translations_collection = db["translations"]
counters_collection = db["counters"]
permission_rules_collection = db["permission_rules"]
roles_collection = db["roles"]
users_collection = db["users"]
languages_collection = db["languages"]
books_collection = db["books"]
