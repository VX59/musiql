import threading
import urllib.parse
import webbrowser
import secrets
import base64
import requests
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from settings import get_settings, Settings

settings: Settings = get_settings()

redirect_uri = "http://127.0.0.1:8888/callback"
code_holder = {}
token_event = threading.Event()  # signals when tokens are ready


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            print("FULL CALLBACK:", self.path)

            if "error" in query:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Spotify auth error")
                return

            if "code" in query:
                code = query["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK - you can close this tab")
                access_token, refresh_token = self.get_bearer_token(code)
                code_holder["code"] = code
                code_holder["access_token"] = access_token
                code_holder["refresh_token"] = refresh_token
                token_event.set()  # signal that tokens are ready
                return

            self.send_response(400)
            self.end_headers()

        def get_bearer_token(self, code):
            auth_header = base64.b64encode(
                f"{settings.spotify_client_id}:{settings.spotify_client_secret}".encode()
            ).decode()
            response = requests.post(
                "https://accounts.spotify.com/api/token",
                headers={
                    "Authorization": f"Basic {auth_header}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            tokens = response.json()
            print("Token response:", tokens)
            return tokens["access_token"], tokens["refresh_token"]

        def log_message(self, format, *args):
            pass  # suppress default logging

    return Handler


def open_browser():
    time.sleep(0.3)
    scope = "user-read-private user-read-email user-modify-playback-state user-read-playback-state user-read-currently-playing"
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": settings.spotify_client_id,
        "scope": scope,
        "redirect_uri": redirect_uri,
        "state": state,
        "show_dialog": True,
    }
    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(
        params
    )
    webbrowser.open(auth_url)


# threading.Thread(target=open_browser, daemon=True).start()

server = HTTPServer(("127.0.0.1", 8888), make_handler())
print("Waiting for Spotify callback...")
server.handle_request()  # blocks until callback is handled, tokens set inside do_GET
server.server_close()

"""# Wait for do_GET to finish setting tokens (handle_request can return slightly before)
token_event.wait(timeout=5)

if code_holder.get("access_token"):
    with open("internal_tools/codes.json", "w") as writer:
        json.dump(code_holder, writer)
    print("Tokens saved to codes.json")
else:
    print("ERROR: no access token received")"""
