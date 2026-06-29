# %%

from src.access import get_access_token

token = get_access_token()
print(token)

# %%
import requests

response = requests.get(
    url="https://api.spotify.com/v1/me/tracks",
    headers={"Authorization": f"Bearer {token}"},
)

# %%

import json

print(response.status_code)
print(json.dumps(json.loads(response.text), indent=4))
