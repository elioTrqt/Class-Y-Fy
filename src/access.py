import base64
from datetime import datetime, timezone
import logging
from dateutil.relativedelta import relativedelta
from pathlib import Path
from typing import Any
import requests
import os
import string
import random
import webbrowser
import urllib.parse
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading


def get_callback_handler(state: str):
    class CallbackHandler(BaseHTTPRequestHandler):
        auth_code: str | None = None

        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            auth_code = params.get("code", [None])[0]
            response_state = params.get("state", [None])[0]
            if state == response_state:
                logging.info("Authentication code received")
                CallbackHandler.auth_code = auth_code
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Auth complete, you can close this tab.")
                threading.Thread(target=self.server.shutdown).start()
            else:
                logging.error("Request did not go through, wrong state")
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"state not matched")
                threading.Thread(target=self.server.shutdown).start()

    return CallbackHandler


def get_authorization_code() -> str:
    state = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    callback_machine = get_callback_handler(state)
    server = HTTPServer(("127.0.0.1", 8888), callback_machine)

    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
        {
            "client_id": os.environ["SPOTIFY_CLIENT"],
            "response_type": "code",
            "redirect_uri": os.environ["REDIRECT_URI"],
            "scope": "user-library-modify user-library-read",
            "state": state,
        }
    )
    _ = webbrowser.open(auth_url)
    server.serve_forever()
    server.server_close()

    if callback_machine.auth_code is None:
        raise Exception("No authentication code registered")

    return callback_machine.auth_code


def get_token_from_code(auth_code: str, store_cache: bool = True) -> dict[str, Any]:
    credentials = os.environ["SPOTIFY_CLIENT"] + ":" + os.environ["SPOTIFY_SECRET"]
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    response = requests.post(
        url="https://accounts.spotify.com/api/token",
        data={
            "code": auth_code,
            "redirect_uri": os.environ["REDIRECT_URI"],
            "grant_type": "authorization_code",
        },
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + encoded,
        },
        json=True,
    )
    if response.status_code != 200:
        raise Exception(
            f"Failed to retrive token : {response.status_code} : {response.text}"
        )

    data = response.json()
    expiration_date = datetime.now(timezone.utc).timestamp() + data["expires_in"] - 60
    data["expires_at"] = expiration_date

    refresh_expire = datetime.now(timezone.utc) + relativedelta(days=181)
    refresh_expire = refresh_expire.timestamp()
    data["refresh_expires"] = refresh_expire

    if not store_cache:
        data["expires_at"] = datetime.fromtimestamp(data["expires_at"], timezone.utc)
        data["refresh_expires"] = datetime.fromtimestamp(
            data["refresh_expires"], timezone.utc
        )
        return data

    cache_file = Path(os.environ["CACHE"]) / "token.json"
    os.makedirs(cache_file.parent, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(data, f)

    data["expires_at"] = datetime.fromtimestamp(data["expires_at"], timezone.utc)
    data["refresh_expires"] = datetime.fromtimestamp(
        data["refresh_expires"], timezone.utc
    )
    return data


def get_token_from_cache() -> dict[str, Any] | None:
    if not os.path.exists(os.environ["CACHE"]):
        return None

    try:
        cache_file = Path(os.environ["CACHE"]) / "token.json"
        with open(cache_file, "r") as f:
            data = json.load(f)
    except Exception:
        return None

    data["expires_at"] = datetime.fromtimestamp(data["expires_at"], timezone.utc)
    data["refresh_expires"] = datetime.fromtimestamp(
        data["refresh_expires"], timezone.utc
    )

    return data


def get_refresh_token(
    token_data: dict[str, Any], store_cache: bool = True
) -> dict[str, Any]:
    credentials = os.environ["SPOTIFY_CLIENT"] + ":" + os.environ["SPOTIFY_SECRET"]
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    response = requests.post(
        url="https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
        },
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + encoded,
        },
        json=True,
    )
    if response.status_code != 200:
        raise Exception(
            f"Failed to retrive token : {response.status_code} : {response.text}"
        )

    data = response.json()
    expiration_date = datetime.now(timezone.utc).timestamp() + data["expires_in"] - 60
    data["expires_at"] = expiration_date
    data["refresh_expires"] = token_data["refresh_expires"].timestamp()

    if not store_cache:
        data["expires_at"] = datetime.fromtimestamp(data["expires_at"], timezone.utc)
        data["refresh_expires"] = datetime.fromtimestamp(
            data["refresh_expires"], timezone.utc
        )
        return data

    cache_file = Path(os.environ["CACHE"]) / "token.json"
    os.makedirs(cache_file.parent, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(data, f)

    data["expires_at"] = datetime.fromtimestamp(data["expires_at"], timezone.utc)
    data["refresh_expires"] = datetime.fromtimestamp(
        data["refresh_expires"], timezone.utc
    )
    return data


def get_access_token() -> str:
    try:
        logging.info("Searching for token in cache")
        token = get_token_from_cache()
        if (
            token is not None
            and token["expires_at"] < datetime.now(timezone.utc).timestamp()
        ):
            logging.info("Returning token directly from cache")
            return token["access_token"]
        elif (
            token is not None
            and token["refresh_expires"] < datetime.now(timezone.utc).timestamp()
        ):
            logging.info("Refreshing token")
            token = get_refresh_token(token["refresh_token"])
            return token["access_token"]
        else:
            logging.info(
                "Either no cache or outdated refresh token, granting new authorization code"
            )
            code = get_authorization_code()
            logging.info("Fetching new token with code")
            token = get_token_from_code(code)
            return token["access_token"]
    except Exception as e:
        logging.error(
            f"Failed to fetch token on first attempt : {e}, trying to wipe cache out"
        )
        cache_file = Path(os.environ["CACHE"]) / "token.json"
        if os.path.exists(cache_file):
            os.remove(cache_file)
            logging.info("Cache removed, granting new authorization code")
            code = get_authorization_code()
            logging.info("Fetching new token with code")
            token = get_token_from_code(code)
            return token["access_token"]
        else:
            raise e
