"""Alertmanager webhook receiver 最简版（M0）：追加写 /alerts/alerts-dump.jsonl。

M1 时整个服务被 oncall ingest 端点替换，因此刻意零依赖（stdlib only），
不引入 FastAPI——落盘格式为每行一个 Alertmanager 回调 JSON（firing/resolved 通用）。
"""

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DUMP_PATH = os.environ.get("DUMP_PATH", "/alerts/alerts-dump.jsonl")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("alert-dumper")


class AlertDumpHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            logger.warning("invalid JSON payload, dropped")
            return

        alerts = payload.get("alerts", [])
        with open(DUMP_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            f.flush()
        statuses = [a.get("status", "?") for a in alerts]
        logger.info("dumped %d alert(s): %s", len(alerts), statuses)
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug(fmt, *args)


def main() -> None:
    port = int(os.environ.get("PORT", "9000"))
    # S104: 容器内必须绑 0.0.0.0 才能被 compose 网络访问，端口不对外发布
    server = ThreadingHTTPServer(("0.0.0.0", port), AlertDumpHandler)  # noqa: S104
    logger.info("alert-dumper listening on :%d, dumping to %s", port, DUMP_PATH)
    server.serve_forever()


if __name__ == "__main__":
    main()
