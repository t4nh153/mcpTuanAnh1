import asyncio
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import websockets

DEFAULT_MCP_ENDPOINT = (
    "wss://api.xiaozhi.me/mcp/?token="
    "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJ1c2VySWQiOjgwMDIwOSwiYWdlbnRJZCI6MTQxNzg3MywiZW5kcG9pbnRJZCI6ImFnZW50XzE0MTc4NzMiLCJwdXJwb3NlIjoibWNwLWVuZHBvaW50IiwiaWF0IjoxNzk1NjA3NzMyLCJleHAiOjE4MjcxNjUzMzJ9."
    "iduaV8EA3nBzNc6mS_s5BgFjbpSiv1NPME7YmGSVd7C27srZM8Rheotn_KP9uG3f7QGT9zGb6VrcuiS269Z9RQ"
)


class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"online","service":"mcp-only"}')

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # Tắt log ping của UptimeRobot cho gọn màn hình


def start_keepalive_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    print(f"[Keep-Alive] Đang mở cổng HTTP {port} cho UptimeRobot", flush=True)
    server.serve_forever()


threading.Thread(target=start_keepalive_server, daemon=True).start()


async def connect_to_server(uri, script_path):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    while True:
        process = None
        try:
            print("[MCP] Đang kết nối tới Xiaozhi Server...", flush=True)
            async with websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=20,
                max_size=10 * 1024 * 1024,
            ) as websocket:
                print("[MCP] Kết nối thành công! Sẵn sàng nhận lệnh Đọc báo & Giá vàng.", flush=True)
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-u", script_path,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=sys.stderr,
                    env=env,
                )

                async def ws_to_proc():
                    async for message in websocket:
                        if isinstance(message, bytes):
                            message = message.decode("utf-8")
                        process.stdin.write((message + "\n").encode("utf-8"))
                        await process.stdin.drain()

                async def proc_to_ws():
                    while True:
                        line = await process.stdout.readline()
                        if not line:
                            break
                        text = line.decode("utf-8", errors="replace").strip()
                        if text:
                            await websocket.send(text)

                done, pending = await asyncio.wait(
                    [asyncio.create_task(ws_to_proc()), asyncio.create_task(proc_to_ws())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
        except Exception as e:
            print(f"[MCP] Ngắt kết nối ({e}). Thử lại sau 5 giây...", flush=True)
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
        await asyncio.sleep(5)


if __name__ == "__main__":
    endpoint = os.environ.get("MCP_ENDPOINT", DEFAULT_MCP_ENDPOINT).strip()
    script = sys.argv[1] if len(sys.argv) > 1 else "main_tools.py"
    asyncio.run(connect_to_server(endpoint, script))
