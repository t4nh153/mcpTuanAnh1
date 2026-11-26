from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, PlainTextResponse, StreamingResponse
import yt_dlp
import urllib.request
import urllib.parse
import json
import shutil
import subprocess
import logging
import os
import time
import uuid
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("xiaozhi-khkt-music")

app = FastAPI(title="Xiaozhi KHKT Music + MCP Server", version="3.2.0")

RAW_COOKIE_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")
RUNTIME_COOKIE_FILE = "/tmp/cookies.txt"

MUSIC_CACHE = {}
STREAM_SESSION = {}
CACHE_LOCK = threading.Lock()
CACHE_TTL = 7200  # Lưu đệm 2 giờ (tránh link hết hạn)


def prepare_cookie_file():
    env_cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if env_cookies:
        with open(RUNTIME_COOKIE_FILE, "w", encoding="utf-8") as f:
            f.write(env_cookies)
        return RUNTIME_COOKIE_FILE

    for path in ["/etc/secrets/cookies.txt", RAW_COOKIE_FILE]:
        if os.path.exists(path) and os.path.getsize(path) > 20:
            shutil.copyfile(path, RUNTIME_COOKIE_FILE)
            return RUNTIME_COOKIE_FILE
    return None


def get_cached_music(query: str):
    clean_k = query.strip().lower()
    with CACHE_LOCK:
        if clean_k in MUSIC_CACHE:
            data, exp = MUSIC_CACHE[clean_k]
            if time.time() < exp:
                return data.copy()
            del MUSIC_CACHE[clean_k]
    return None


def set_cached_music(query: str, data: dict):
    if not data or not data.get("url"):
        return
    clean_k = query.strip().lower()
    with CACHE_LOCK:
        now = time.time()
        for k in [k for k, (_, exp) in MUSIC_CACHE.items() if now >= exp]:
            del MUSIC_CACHE[k]
        for sid in [k for k, v in STREAM_SESSION.items() if now - v["ts"] >= CACHE_TTL]:
            del STREAM_SESSION[sid]
        MUSIC_CACHE[clean_k] = (data.copy(), now + CACHE_TTL)


def can_server_fetch_url(url: str, headers: dict) -> bool:
    """Kiểm tra nhanh xem IP Render có đọc được luồng nhạc không (chống lỗi 403 khóa IP)"""
    try:
        h = dict(headers or {"User-Agent": "Mozilla/5.0"})
        h["Range"] = "bytes=0-1024"
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status in (200, 206)
    except Exception as e:
        logger.warning(f"Link bị chặn IP trên Render ({e})")
        return False


def fetch_lrclib_lyrics(query: str) -> str:
    try:
        req_url = f"https://lrclib.net/api/search?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(req_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            if resp.status == 200:
                items = json.loads(resp.read().decode("utf-8"))
                for item in items:
                    if item.get("syncedLyrics"):
                        return item["syncedLyrics"]
                if items and items[0].get("plainLyrics"):
                    return "[00:00.00]" + items[0]["plainLyrics"].replace("\n", "\n[00:00.00]")
    except Exception:
        pass
    return ""


def is_hls_or_manifest(url: str, proto: str) -> bool:
    u, p = (url or "").lower(), (proto or "").lower()
    return any(k in p for k in ["m3u8", "hls", "dash"]) or ".m3u8" in u or ".mpd" in u


def find_best_progressive_audio(formats: list) -> dict:
    candidates = []
    for f in formats or []:
        url = f.get("url", "")
        proto = f.get("protocol") or ""
        ext = (f.get("ext") or "").lower()
        acodec = (f.get("acodec") or "").lower()
        vcodec = (f.get("vcodec") or "none").lower()
        abr = f.get("abr") or f.get("tbr") or 128

        if not url.startswith("http") or is_hls_or_manifest(url, proto) or acodec == "none":
            continue
        score = 100 if ext == "mp3" else (80 if ext in ["m4a", "aac"] else 40)
        if vcodec == "none":
            score += 40
        candidates.append((score, float(abr) if abr else 128.0, f))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0][2]


def extract_music(query: str):
    cached = get_cached_music(query)
    if cached:
        return cached

    cookie_path = prepare_cookie_file()

    # TẦNG 1: Nếu đã gắn cookies.txt vào Secret Files trên Render -> Bóc trực tiếp YouTube đúng IP Render!
    if cookie_path:
        ydl_yt = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 8,
            "cookiefile": cookie_path,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_yt) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                entries = info.get("entries", []) if info else []
                if entries:
                    v = entries[0]
                    best_fmt = find_best_progressive_audio(v.get("formats", [])) or v
                    headers = best_fmt.get("http_headers") or {"User-Agent": "Mozilla/5.0"}
                    if best_fmt.get("url") and can_server_fetch_url(best_fmt["url"], headers):
                        res = {
                            "source": "youtube",
                            "title": v.get("title", query),
                            "artist": v.get("uploader", "YouTube"),
                            "duration": v.get("duration", 0),
                            "ext": (best_fmt.get("ext") or "m4a").lower(),
                            "headers": headers,
                            "url": best_fmt["url"],
                        }
                        logger.info(f"[YOUTUBE DIRECT] Thành công: {res['title']}")
                        set_cached_music(query, res)
                        return res
        except Exception as e:
            logger.warning(f"[YOUTUBE DIRECT] Lỗi ({e}) -> Chuyển sang SoundCloud...")

    # TẦNG 2 (Mặc định siêu tốc, sạch quảng cáo, không cần cookie, trùng 100% IP Render): SoundCloud
    ydl_sc = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 10,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_sc) as ydl:
            info = ydl.extract_info(f"scsearch5:{query}", download=False)
            for v in (info.get("entries") or []):
                if not v:
                    continue
                # Bỏ qua các bản preview ngắn 30 giây nếu có bản đầy đủ
                if (v.get("duration") or 0) > 0 and v.get("duration") < 40:
                    continue
                best_fmt = find_best_progressive_audio(v.get("formats", []))
                target = best_fmt if best_fmt else v
                url = target.get("url", "")
                if url and not is_hls_or_manifest(url, target.get("protocol", "")):
                    headers = target.get("http_headers") or {"User-Agent": "Mozilla/5.0"}
                    res = {
                        "source": "soundcloud",
                        "title": v.get("title", query),
                        "artist": v.get("uploader", "SoundCloud"),
                        "duration": v.get("duration", 0),
                        "ext": (target.get("ext") or "mp3").lower(),
                        "headers": headers,
                        "url": url,
                    }
                    logger.info(f"[SOUNDCLOUD] Thành công: {res['title']}")
                    set_cached_music(query, res)
                    return res
                # Nếu SoundCloud trả HLS m3u8, FFmpeg trên Render vẫn đọc và nén sang MP3 mượt 100%!
                if v.get("formats"):
                    f0 = v["formats"][-1]
                    if f0.get("url"):
                        res = {
                            "source": "soundcloud_hls_to_mp3",
                            "title": v.get("title", query),
                            "artist": v.get("uploader", "SoundCloud"),
                            "duration": v.get("duration", 0),
                            "ext": "mp3",
                            "headers": f0.get("http_headers") or {"User-Agent": "Mozilla/5.0"},
                            "url": f0["url"],
                        }
                        logger.info(f"[SOUNDCLOUD FFMPEG] Thành công: {res['title']}")
                        set_cached_music(query, res)
                        return res
    except Exception as e:
        logger.error(f"[SOUNDCLOUD] Lỗi: {e}")

    return None


def create_stream_session(track: dict, lyrics: str = "") -> tuple:
    sid = uuid.uuid4().hex[:10]
    with CACHE_LOCK:
        STREAM_SESSION[sid] = {**track, "lyrics": lyrics, "ts": time.time()}
    return f"/proxy_mp3?id={sid}", f"/proxy_lyric?id={sid}"


@app.get("/")
def home():
    with CACHE_LOCK:
        cnt = len(MUSIC_CACHE)
    return {
        "status": "online",
        "version": "3.2.0",
        "ffmpeg_ready": bool(shutil.which("ffmpeg")),
        "cookie_loaded": bool(prepare_cookie_file()),
        "cached_songs": cnt,
    }


@app.get("/stream_pcm")
def stream_pcm(song: str = Query(...), artist: str = Query("")):
    q = f"{song} {artist}".strip()
    res = extract_music(q)
    if not res:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài hát")

    lyrics = fetch_lrclib_lyrics(q) or f"[00:00.00]{res['title']} - {res['artist']}"
    audio_rel_path, lyric_rel_path = create_stream_session(res, lyrics)

    return {
        "title": res["title"],
        "artist": res["artist"],
        "duration": res.get("duration", 0),
        "source": res["source"],
        "audio_url": audio_rel_path,
        "lyric_url": lyric_rel_path,
    }


@app.get("/play")
def play_json(request: Request, q: str = Query(...)):
    res = extract_music(q)
    if not res:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài hát")
    audio_rel_path, _ = create_stream_session(res)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return {
        "status": "success",
        "data": {
            "source": res["source"],
            "title": res["title"],
            "duration": res.get("duration", 0),
            "url": f"{proto}://{host}{audio_rel_path}",
        },
    }


@app.get("/redirect")
def play_redirect(request: Request, q: str = Query(...)):
    res = extract_music(q)
    if not res:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài hát")
    audio_rel_path, _ = create_stream_session(res)
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return RedirectResponse(url=f"{proto}://{host}{audio_rel_path}", status_code=302)


@app.get("/proxy_mp3")
def proxy_mp3(id: str = Query(...)):
    with CACHE_LOCK:
        track = STREAM_SESSION.get(id)
    if not track:
        raise HTTPException(status_code=404, detail="Phiên phát nhạc đã hết hạn")

    raw_url = track["url"]
    headers = track.get("headers") or {"User-Agent": "Mozilla/5.0"}
    ua = headers.get("User-Agent", "Mozilla/5.0")

    def generate_mp3():
        proc = subprocess.Popen(
            [
                "ffmpeg", "-nostdin", "-loglevel", "error",
                "-user_agent", ua,
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                "-i", raw_url,
                "-vn", "-acodec", "libmp3lame", "-ab", "128k", "-ar", "44100", "-ac", "2",
                "-f", "mp3", "pipe:1",
            ],
            stdout=subprocess.PIPE,
        )
        try:
            while True:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.terminate()

    return StreamingResponse(generate_mp3(), media_type="audio/mpeg")


@app.get("/proxy_lyric")
def proxy_lyric(id: str = Query(...)):
    with CACHE_LOCK:
        track = STREAM_SESSION.get(id)
    if not track or not track.get("lyrics"):
        return PlainTextResponse("[00:00.00]Đang phát nhạc...")
    return PlainTextResponse(track["lyrics"])
