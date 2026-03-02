import json
import os
import time
from urllib.parse import urlparse, parse_qs

from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

import config


def _load_token():
    if not os.path.exists(config.TOKEN_FILE):
        return None
    with open(config.TOKEN_FILE) as f:
        data = json.load(f)
    saved_at = data.get("saved_at", 0)
    if time.time() - saved_at > 23 * 3600:
        return None
    return data.get("access_token")


def _save_token(access_token: str):
    with open(config.TOKEN_FILE, "w") as f:
        json.dump({"access_token": access_token, "saved_at": time.time()}, f)


def get_access_token() -> str:
    token = _load_token()
    if token:
        return token

    # OAuth2 flow
    session = fyersModel.SessionModel(
        client_id=config.FYERS_APP_ID,
        secret_key=config.FYERS_SECRET_KEY,
        redirect_uri=config.FYERS_REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
    )
    auth_url = session.generate_authcode()
    print("\n--- Fyers Authentication Required ---")
    print(f"Open this URL in your browser:\n{auth_url}\n")
    redirect_url = input("After login, paste the full redirect URL here: ").strip()

    parsed = urlparse(redirect_url)
    auth_code = parse_qs(parsed.query).get("auth_code", [None])[0]
    if not auth_code:
        raise ValueError("Could not extract auth_code from redirect URL")

    session.set_token(auth_code)
    response = session.generate_token()
    access_token = response.get("access_token")
    if not access_token:
        raise ValueError(f"Token exchange failed: {response}")

    _save_token(access_token)
    print("Access token saved.")
    return access_token


def get_fyers_instance() -> fyersModel.FyersModel:
    token = get_access_token()
    fyers = fyersModel.FyersModel(
        client_id=config.FYERS_APP_ID,
        token=token,
        log_path="",
    )
    return fyers
