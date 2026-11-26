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

app = FastAPI(title="Xiaozhi KHKT Music + MCP Server", version="3.6.0")

UPSTREAM_TVBOX_API = os.environ.get("UPSTREAM_API", "https://xiaozhi-music-api.onrender.com").rstrip("/")
RAW_COOKIE_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")
RUNTIME_COOKIE_FILE = "/tmp/cookies.txt"

MUSIC_CACHE = {}
STREAM_SESSION = {}
CACHE_LOCK = threading.Lock()
CACHE_TTL = 7200  # Lưu đệm 2 giờ


def build_silent_mp3_chunk() -> bytes:
    """Tạo sẵn 0.4 giây khung MP3 tĩnh chuẩn 44.1kHz Stereo 128kbps để giữ kết nối chống Timeout"""
    if not shutil.which("ffmpeg"):
        return b""
    try:
        raw_zeros = b"\x00" * (44100 * 2 * 2 * 4 // 10)  # 0.4s PCM 16-bit stereo
        proc = subprocess.Popen(
            [
                "ffmpeg", "-nostdin", "-loglevel", "error",
                "-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "pipe:0",
                "-acodec", "libmp3lame", "-ab", "128k",
                "-write_xing", "0", "-id3v2_version", "0",
                "-f", "mp3", "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        out, _ = proc.communicate(input=raw_zeros, timeout=5)
        return out
    except Exception:
        return b""


SILENT_MP3_CHUNK = build_silent_mp3_chunk()


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
                logger.info(f"[CACHE HIT] '{clean_k}'")
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


def can_server_fetch_url(url: str, headers: dict = None) -> bool:
    try:
        h = dict(headers or {"User-Agent": "Mozilla/5.0"})
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=3) as r:
            chunk = r.read(256)
            return r.status in (200, 206) and len(chunk) > 0
    except Exception as e:
        logger.warning(f"URL bị chặn đọc trực tiếp ({e})")
        return False


def fetch_lrclib_lyrics(query: str) -> str:
    try:
        req_url = f"https://lrclib.net/api/search?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(req_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
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


# =====================================================================
# BỘ ĐIỀU PHỐI SIÊU TỐC DÀNH RIÊNG CHO RENDER 0.1 CPU
# =====================================================================
def extract_music(query: str):
    cached = get_cached_music(query)
    if cached:
        return cached

    cookie_path = prepare_cookie_file()

    # TẦNG 1A: YouTube trực tiếp (Tắt tải HLS/DASH để chạy cực nhẹ trên 0.1 CPU)
    clients = ["android_vr", "web"] if cookie_path else ["android_vr"]
    ydl_yt = {
        "format": "bestaudio[protocol^=http][protocol!*=m3u8][protocol!*=dash]/bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 5,
        "retries": 1,
        "extractor_retries": 1,
        "extractor_args": {
            "youtube": {
                "player_client": clients,
                "skip": ["hls", "dash", "translated_subs"],
            }
        },
    }
    if cookie_path:
        ydl_yt["cookiefile"] = cookie_path

    try:
        with yt_dlp.YoutubeDL(ydl_yt) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            entries = info.get("entries", []) if info else []
            if entries:
                v = entries[0]
                best_fmt = find_best_progressive_audio(v.get("formats", [])) or v
                headers = best_fmt.get("http_headers") or {"User-Agent": "Mozilla/5.0"}
                url = best_fmt.get("url", "")
                if url and can_server_fetch_url(url, headers):
                    res = {
                        "source": "youtube_direct",
                        "title": v.get("title", query),
                        "artist": v.get("uploader", "YouTube"),
                        "duration": v.get("duration", 0),
                        "ext": (best_fmt.get("ext") or "m4a").lower(),
                        "headers": headers,
                        "url": url,
                    }
                    logger.info(f"[YOUTUBE DIRECT] Thành công: {res['title']}")
                    set_cached_music(query, res)
                    return res
    except Exception as e:
        logger.warning(f"[YOUTUBE DIRECT] Lỗi ({e}) -> Thử Upstream...")

    # TẦNG 1B: Gọi qua API TV Box bên kia (Timeout ngắn 5s chống treo)
    if UPSTREAM_TVBOX_API:
        try:
            api_url = f"{UPSTREAM_TVBOX_API}/play?q={urllib.parse.quote(query)}"
            req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    body = json.loads(resp.read().decode("utf-8"))
                    if body.get("status") == "success" and body.get("data", {}).get("url"):
                        d = body["data"]
                        raw_u = d["url"]
                        usable_url = None
                        if can_server_fetch_url(raw_u):
                            usable_url = raw_u
                        else:
                            relayed = f"{UPSTREAM_TVBOX_API}/stream?m3u8={urllib.parse.quote(raw_u, safe='')}"
                            if can_server_fetch_url(relayed):
                                usable_url = relayed
                        if usable_url:
                            res = {
                                "source": f"youtube_upstream_{d.get('source', 'api')}",
                                "title": d.get("title", query),
                                "artist": "YouTube Music",
                                "duration": d.get("duration", 0),
                                "ext": "mp3",
                                "headers": {"User-Agent": "Mozilla/5.0"},
                                "url": usable_url,
                            }
                            logger.info(f"[YOUTUBE UPSTREAM] Thành công: {res['title']}")
                            set_cached_music(query, res)
                            return res
        except Exception as e:
            logger.warning(f"[UPSTREAM] Bỏ qua ({e}) -> Chuyển SoundCloud...")

    # TẦNG 2: Dự phòng SoundCloud siêu tốc (Chỉ quét 2 bài đầu tiên tốn ~1.5s)
    ydl_sc = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 5,
        "retries": 1,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_sc) as ydl:
            info = ydl.extract_info(f"scsearch2:{query}", download=False)
            for v in (info.get("entries") or []):
                if not v:
                    continue
                if (v.get("duration") or 0) > 0 and v.get("duration") < 40:
                    continue
                best_fmt = find_best_progressive_audio(v.get("formats", [])) or v
                url = best_fmt.get("url") or (v["formats"][-1]["url"] if v.get("formats") else "")
                if url:
                    res = {
                        "source": "soundcloud_backup",
                        "title": v.get("title", query),
                        "artist": v.get("uploader", "SoundCloud"),
                        "duration": v.get("duration", 0),
                        "ext": "mp3",
                        "headers": best_fmt.get("http_headers") or {"User-Agent": "Mozilla/5.0"},
                        "url": url,
                    }
                    logger.info(f"[SOUNDCLOUD] Thành công: {res['title']}")
                    set_cached_music(query, res)
                    return res
    except Exception as e:
        logger.error(f"[SOUNDCLOUD] Lỗi: {e}")

    return None


def start_async_session(query: str, fallback_title: str, fallback_artist: str) -> tuple:
    sid = uuid.uuid4().hex[:10]
    ready_event = threading.Event()

    cached = get_cached_music(query)
    if cached:
        ready_event.set()
        with CACHE_LOCK:
            STREAM_SESSION[sid] = {
                "track": cached,
                "ready": ready_event,
                "query": query,
                "lyrics": None,
                "ts": time.time(),
            }
        return sid, cached, True

    with CACHE_LOCK:
        STREAM_SESSION[sid] = {
            "track": None,
            "ready": ready_event,
            "query": query,
            "fallback_title": fallback_title,
            "fallback_artist": fallback_artist,
            "lyrics": None,
            "ts": time.time(),
        }

    def _worker():
        try:
            res = extract_music(query)
            with CACHE_LOCK:
                if sid in STREAM_SESSION:
                    STREAM_SESSION[sid]["track"] = res
        finally:
            ready_event.set()

    threading.Thread(target=_worker, daemon=True).start()
    # Chờ tối đa 8 giây (dưới xa ngưỡng Timeout 30 giây của ESP32)
    finished = ready_event.wait(timeout=8.0)
    with CACHE_LOCK:
        track = STREAM_SESSION.get(sid, {}).get("track")
    return sid, track, finished


@app.get("/")
def home():
    with CACHE_LOCK:
        cnt = len(MUSIC_CACHE)
    return {
        "status": "online",
        "version": "3.6.0 (Zero-Timeout ESP32 Edition)",
        "ffmpeg_ready": bool(shutil.which("ffmpeg")),
        "cookie_loaded": bool(prepare_cookie_file()),
        "cached_songs": cnt,
    }


@app.get("/stream_pcm")
def stream_pcm(song: str = Query(...), artist: str = Query("")):
    q = f"{song} {artist}".strip()
    sid, res, finished = start_async_session(q, song, artist or "YouTube Music")

    if finished and not res:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài hát")

    title = res["title"] if res else song
    art = res["artist"] if res else (artist or "Đang tải luồng nhạc...")
    dur = res.get("duration", 0) if res else 0
    src = res["source"] if res else "async_buffering"

    return {
        "title": title,
        "artist": art,
        "duration": dur,
        "source": src,
        "audio_url": f"/proxy_mp3?id={sid}",
        "lyric_url": f"/proxy_lyric?id={sid}",
    }


@app.get("/play")
def play_json(request: Request, q: str = Query(...)):
    res = extract_music(q)
    if not res:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài hát")
    sid, _, _ = start_async_session(q, res["title"], res["artist"])
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return {
        "status": "success",
        "data": {
            "source": res["source"],
            "title": res["title"],
            "duration": res.get("duration", 0),
            "url": f"{proto}://{host}/proxy_mp3?id={sid}",
        },
    }


@app.get("/redirect")
def play_redirect(request: Request, q: str = Query(...)):
    res = extract_music(q)
    if not res:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài hát")
    sid, _, _ = start_async_session(q, res["title"], res["artist"])
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    return RedirectResponse(url=f"{proto}://{host}/proxy_mp3?id={sid}", status_code=302)


@app.get("/proxy_mp3")
def proxy_mp3(id: str = Query(...)):
    with CACHE_LOCK:
        session = STREAM_SESSION.get(id)
    if not session:
        raise HTTPException(status_code=404, detail="Phiên phát nhạc đã hết hạn")

    ready_event: threading.Event = session["ready"]

    def generate_mp3():
        # Nếu luồng tìm link vẫn đang chạy trên 0.1 CPU, gửi khung MP3 tĩnh giữ sống kết nối cho ESP32
        waited = 0
        while not ready_event.wait(timeout=0.4) and waited < 50:
            waited += 1
            if SILENT_MP3_CHUNK:
                yield SILENT_MP3_CHUNK

        with CACHE_LOCK:
            track = STREAM_SESSION.get(id, {}).get("track")
        if not track or not track.get("url"):
            return

        raw_url = track["url"]
        headers = track.get("headers") or {"User-Agent": "Mozilla/5.0"}
        ua = headers.get("User-Agent", "Mozilla/5.0")

        proc = subprocess.Popen(
            [
                "ffmpeg", "-nostdin", "-loglevel", "error",
                "-user_agent", ua,
                "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                "-i", raw_url,
                "-vn", "-acodec", "libmp3lame", "-ab", "128k", "-ar", "44100", "-ac", "2",
                "-write_xing", "0", "-id3v2_version", "0",
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
            if proc.poll() is None:
                proc.terminate()

    return StreamingResponse(generate_mp3(), media_type="audio/mpeg")


@app.get("/proxy_lyric")
def proxy_lyric(id: str = Query(...)):
    with CACHE_LOCK:
        session = STREAM_SESSION.get(id)
    if not session:
        return PlainTextResponse("[00:00.00]Đang phát nhạc...")

    ready_event: threading.Event = session["ready"]
    ready_event.wait(timeout=5.0)

    with CACHE_LOCK:
        session = STREAM_SESSION.get(id, {})
        track = session.get("track") or {}
        cached_lyrics = session.get("lyrics")

    if cached_lyrics is None:
        q = session.get("query") or track.get("title", "")
        title = track.get("title") or session.get("fallback_title", "Đang phát nhạc")
        artist = track.get("artist") or session.get("fallback_artist", "")
        lyrics = fetch_lrclib_lyrics(q) or f"[00:00.00]{title} - {artist}"
        with CACHE_LOCK:
            if id in STREAM_SESSION:
                STREAM_SESSION[id]["lyrics"] = lyrics
    else:
        lyrics = cached_lyrics

    return PlainTextResponse(lyrics)
