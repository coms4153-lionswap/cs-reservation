import jwt
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGO")

user_id = 11

role = "admin" if user_id == 11 else "user"

now = datetime.now(timezone.utc)
payload = {"user_id": user_id, "role": role, "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=24)).timestamp())}

token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

print(token)