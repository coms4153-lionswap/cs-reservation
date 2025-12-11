import jwt
from datetime import datetime, timedelta

SECRET_KEY = "LION_SWAP_GOAT_IS_THE_KEY"
ALGO = "HS256"

user_id = 11

role = "admin" if user_id == 11 else "user"

now = datetime.utcnow()
payload = { "user_id": user_id, "role": role, "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=24)).timestamp()),
}

token = jwt.encode(payload, SECRET_KEY, algorithm=ALGO)

print(token)