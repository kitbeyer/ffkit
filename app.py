import json
import logging
import mimetypes
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import live_scoreboard as ls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ffkit.web")

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
LEAGUE_SCOREBOARD_RE = re.compile(r"^/api/league/([^/]+)/scoreboard$")


class ScoreboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, rel_path: str) -> None:
        safe_path = os.path.normpath(os.path.join(WEB_DIR, rel_path.lstrip("/")))
        if not safe_path.startswith(WEB_DIR) or not os.path.isfile(safe_path):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        mime, _ = mimetypes.guess_type(safe_path)
        with open(safe_path, "rb") as f:
            content = f.read()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path == "/api/health":
                self._send_json({"status": "ok"})
                return

            if path == "/api/nfl-state":
                self._send_json(ls.get_nfl_state())
                return

            if path == "/api/user":
                query = (qs.get("q") or [""])[0].strip()
                if not query:
                    self._send_json({"error": "Missing Sleeper username or user ID"}, HTTPStatus.BAD_REQUEST)
                    return

                user = ls.get_user(query)
                if not user:
                    self._send_json({"error": f"Sleeper user '{query}' not found"}, HTTPStatus.NOT_FOUND)
                    return

                nfl_state = ls.get_nfl_state()
                season_str = (qs.get("season") or [""])[0].strip()
                season = int(season_str) if season_str.isdigit() else nfl_state["season"]
                leagues = ls.get_user_leagues_summary(user["user_id"], season)
                self._send_json({
                    "user": user,
                    "nfl_state": nfl_state,
                    "season": season,
                    "leagues": leagues,
                })
                return

            match = LEAGUE_SCOREBOARD_RE.match(path)
            if match:
                league_id = match.group(1)
                nfl_state = ls.get_nfl_state()
                season_str = (qs.get("season") or [""])[0].strip()
                week_str = (qs.get("week") or [""])[0].strip()
                user_id = (qs.get("user_id") or [None])[0]
                refresh = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")

                season = int(season_str) if season_str.isdigit() else nfl_state["season"]
                week = int(week_str) if week_str.isdigit() else nfl_state["week"]

                data = ls.get_league_scoreboard(
                    league_id=league_id,
                    season=season,
                    week=week,
                    user_id=user_id,
                    refresh_cache=refresh,
                )
                self._send_json(data)
                return

            if path == "/" or path == "":
                self._send_file("index.html")
                return

            self._send_file(path)
        except Exception as exc:
            logger.exception("Request failed for %s", self.path)
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    server = ThreadingHTTPServer((host, port), ScoreboardHandler)
    logger.info("Sleeper Live Scoreboard running at http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    run_server(host=host, port=port)
