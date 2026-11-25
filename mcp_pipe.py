import asyncio
import websockets
import subprocess
import sys
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Link MCP Endpoint của thiết bị agent_1417873 đã được gắn sẵn:
DEFAULT_MCP_ENDPOINT = (
    "wss://api.xiaozhi.me/mcp/?token="
    "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJ1c2VySWQiOjgwMDIwOSwiYWdlbnRJZCI6MTQxNzg3MywiZW5kcG9pbnRJZCI6ImFnZW50XzE0MTc4NzMiLCJwdXJwb3NlIjoibWNwLWVuZHBvaW50IiwiaWF0IjoxNzk1NjA3NzMyLCJleHAiOjE4MjcxNjUzMzJ9."
    "iduaV8EA3nBzNc6mS_s5BgFjbpSiv1NPME7YmGSVd7C27srZM8Rheotn_KP9uG3f7QGT9zGb6VrcuiS269Z9RQ"
)

# 1. Mở cổng Web siêu nhẹ chống ngủ đông trên Render
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Xiaozhi KHKT MCP Server is running 24/7!".encode("utf-8"))

    def log_message(self, format, *args):
        return

def start_keep_alive():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), KeepAliveHandler)
    print(f"[HTTP] Đã mở cổng giữ thức tại port {port}", flush=True)
    server.serve_forever()

threading.Thread(target=start_keep_alive, daemon=True).start()

# 2. Đường ống nối MCP Python với Server Xiaozhi
async def connect_to_server(uri, script_path):
    while True:
        try:
            print("[MCP] Đang kết nối tới Xiaozhi Server...", flush=True)
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as websocket:
                print("[MCP] Kết nối thành công! Sẵn sàng nhận lệnh giọng nói.", flush=True)
                process = await asyncio.create_subprocess_exec(
                    sys.executable, script_path,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=sys.stderr
                )

                async def ws_to_proc():
                    try:
                        async for message in websocket:
                            if isinstance(message, bytes):
                                message = message.decode("utf-8")
                            process.stdin.write((message + "\n").encode("utf-8"))
                            await process.stdin.drain()
                    except Exception as e:
                        print(f"[WS - Proc] Ngắt luồng: {e}", flush=True)
                    finally:
                        if process.returncode is None:
                            process.terminate()

                async def proc_to_ws():
                    try:
                        while True:
                            line = await process.stdout.readline()
                            if not line:
                                break
                            await websocket.send(line.decode("utf-8").strip())
                    except Exception as e:
                        print(f"[Proc - WS] Ngắt luồng: {e}", flush=True)

                await asyncio.gather(ws_to_proc(), proc_to_ws())
        except Exception as e:
            print(f"[MCP] Mất kết nối ({e}). Thử lại sau 5 giây...", flush=True)
            await asyncio.sleep(5)

if __name__ == "__main__":
    # Ưu tiên lấy biến môi trường trên Render (nếu có), nếu không sẽ dùng luôn link gắn sẵn ở trên
    endpoint = os.environ.get("MCP_ENDPOINT", DEFAULT_MCP_ENDPOINT).strip()
    script = sys.argv[1] if len(sys.argv) > 1 else "main_tools.py"
    asyncio.run(connect_to_server(endpoint, script))