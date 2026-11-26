import asyncio
import os
import subprocess
import sys
import threading

import uvicorn
import websockets
from music_server import app as music_app

# Khóa kết nối MCP mặc định của thiết bị agent_1417873 (ưu tiên đọc từ biến môi trường MCP_ENDPOINT trên Render)
DEFAULT_MCP_ENDPOINT = (
    "wss://api.xiaozhi.me/mcp/?token="
    "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJ1c2VySWQiOjgwMDIwOSwiYWdlbnRJZCI6MTQxNzg3MywiZW5kcG9pbnRJZCI6ImFnZW50XzE0MTc4NzMiLCJwdXJwb3NlIjoibWNwLWVuZHBvaW50IiwiaWF0IjoxNzk1NjA3NzMyLCJleHAiOjE4MjcxNjUzMzJ9."
    "iduaV8EA3nBzNc6mS_s5BgFjbpSiv1NPME7YmGSVd7C27srZM8Rheotn_KP9uG3f7QGT9zGb6VrcuiS269Z9RQ"
)


def start_music_and_keepalive():
    port = int(os.environ.get("PORT", 10000))
    print(f"[Music+HTTP] Đang mở Server Nhạc & Keep-Alive tại port {port}", flush=True)
    uvicorn.run(music_app, host="0.0.0.0", port=port, log_level="warning")


threading.Thread(target=start_music_and_keepalive, daemon=True).start()


async def connect_to_server(uri, script_path):
    while True:
        try:
            print("[MCP] Đang kết nối tới Xiaozhi Server...", flush=True)
            async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as websocket:
                print("[MCP] Kết nối thành công! Sẵn sàng nhận lệnh Đọc báo & Giá vàng.", flush=True)
                process = await asyncio.create_subprocess_exec(
                    sys.executable, script_path,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=sys.stderr,
                )

                async def ws_to_proc():
                    try:
                        async for message in websocket:
                            if isinstance(message, bytes):
                                message = message.decode("utf-8")
                            process.stdin.write((message + "\n").encode("utf-8"))
                            await process.stdin.drain()
                    except Exception as e:
                        print(f"[WS -> Proc] Ngắt luồng: {e}", flush=True)
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
                        print(f"[Proc -> WS] Ngắt luồng: {e}", flush=True)

                await asyncio.gather(ws_to_proc(), proc_to_ws())
        except Exception as e:
            print(f"[MCP] Mất kết nối ({e}). Thử lại sau 5 giây...", flush=True)
            await asyncio.sleep(5)


if __name__ == "__main__":
    endpoint = os.environ.get("MCP_ENDPOINT", DEFAULT_MCP_ENDPOINT).strip()
    script = sys.argv[1] if len(sys.argv) > 1 else "main_tools.py"
    asyncio.run(connect_to_server(endpoint, script))
