"""
视频提取音频 - 后端服务
=======================
提供三类接口：
1. /api/parse-link  — 解析短视频链接（抖音/快手/B站/小红书/火山）
2. /api/upload/*     — 分片上传本地视频文件（支持大文件）
3. /api/task/*       — 查询任务状态、获取音频文件
"""

import os
import re
import uuid
import json
import shutil
import asyncio
import tempfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ── 路径配置 ──
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"       # 分片暂存
TASK_DIR = BASE_DIR / "tasks"            # 任务目录（视频+音频）
UPLOAD_DIR.mkdir(exist_ok=True)
TASK_DIR.mkdir(exist_ok=True)

# ── ffmpeg 路径 ──
def get_ffmpeg_path():
    """优先系统 ffmpeg，其次 imageio-ffmpeg 自带"""
    import subprocess
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return "ffmpeg"
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"

FFMPEG = get_ffmpeg_path()

app = FastAPI(title="视频提取音频 API")

# 允许所有来源（生产环境应限制为小程序域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 任务管理（内存存储，生产环境应换 Redis） ──
TASKS: dict = {}  # task_id -> {status, progress, message, video_path, audio_path, ...}


def create_task(task_type: str) -> str:
    """创建任务"""
    task_id = uuid.uuid4().hex[:12]
    task_dir = TASK_DIR / task_id
    task_dir.mkdir(exist_ok=True)
    TASKS[task_id] = {
        "id": task_id,
        "type": task_type,      # "link" or "upload"
        "status": "pending",    # pending / downloading / extracting / done / error
        "progress": 0,
        "message": "",
        "video_path": None,
        "audio_path": None,
        "audio_format": "mp3",
        "created_at": asyncio.get_event_loop().time(),
    }
    return task_id


# ════════════════════════════════════════
#  1. 短视频链接解析
# ════════════════════════════════════════

SUPPORTED_PLATFORMS = {
    "douyin": ["douyin.com", "iesdouyin.com", "v.douyin.com"],
    "kuaishou": ["kuaishou.com", "v.kuaishou.com", "gifshow.com"],
    "huoshan": ["huoshan.com", "douyin.com/huoshan"],
    "bilibili": ["bilibili.com", "b23.tv"],
    "xiaohongshu": ["xiaohongshu.com", "xhslink.com"],
}


def detect_platform(url: str) -> str:
    """根据 URL 判断平台"""
    url_lower = url.lower()
    for platform, domains in SUPPORTED_PLATFORMS.items():
        if any(d in url_lower for d in domains):
            return platform
    return "unknown"


def extract_url(text: str) -> str:
    """从分享文案中提取真正的视频 URL"""
    # 匹配 http/https 开头的 URL
    match = re.search(r'https?://[^\s<>"\'，。]+', text)
    if match:
        return match.group(0).rstrip('/')
    return text.strip()


@app.post("/api/parse-link")
async def parse_link(url: str = Form(...)):
    """
    解析短视频链接并提取音频
    返回 task_id，后台异步处理
    """
    # 从分享文案中提取真正的 URL
    url = extract_url(url)
    platform = detect_platform(url)
    if platform == "unknown":
        raise HTTPException(400, "不支持的视频链接，目前支持抖音、快手、火山、B站、小红书")

    task_id = create_task("link")
    TASKS[task_id]["message"] = f"正在解析 {platform} 链接..."

    # 异步执行下载+提取
    asyncio.create_task(_process_link(task_id, url, platform))
    return {"task_id": task_id, "platform": platform}


async def _process_link(task_id: str, url: str, platform: str):
    """后台任务：下载视频 → 提取音频"""
    task = TASKS[task_id]
    task_dir = TASK_DIR / task_id

    try:
        # ── Step 1: 下载视频 ──
        task["status"] = "downloading"
        task["message"] = f"正在下载 {platform} 视频..."
        task["progress"] = 10

        video_path = task_dir / "video.mp4"
        downloaded = await asyncio.to_thread(_download_video, url, str(video_path))

        if not downloaded:
            task["status"] = "error"
            task["message"] = "视频下载失败，可能是链接无效、需要登录或平台反爬，请换链接重试"
            return

        task["video_path"] = str(downloaded)
        task["progress"] = 60
        task["message"] = "下载完成，正在提取音频..."

        # ── Step 2: 提取音频 ──
        task["status"] = "extracting"
        audio_path = task_dir / "audio.mp3"
        await asyncio.to_thread(_extract_audio, str(downloaded), str(audio_path))

        task["audio_path"] = str(audio_path)
        task["status"] = "done"
        task["progress"] = 100
        task["message"] = "音频提取完成"

        # 获取音频文件大小
        task["audio_size"] = audio_path.stat().st_size

        # 删除视频文件节省空间
        try:
            os.unlink(str(downloaded))
        except OSError:
            pass

    except Exception as e:
        task["status"] = "error"
        task["message"] = f"处理失败: {str(e)}"


def _get_fresh_cookies(url: str) -> str | None:
    """获取平台 Cookie，保存为 Netscape 格式文件"""
    import requests

    platform = detect_platform(url)
    cookie_file = str(Path(tempfile.gettempdir()) / f"cookies_{platform}.txt")

    # 抖音需要 JS 生成的 Cookie（ttwid 等），用 Playwright 无头浏览器获取
    if platform == "douyin":
        pw_result = _get_cookies_playwright("https://www.douyin.com/", cookie_file)
        if pw_result:
            return pw_result
        # Playwright 不可用或失败，尝试 requests 兜底
        print("[cookies] Playwright failed, trying requests fallback")

    # 其他平台：requests 访问首页获取 Cookie
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })

    homepage_map = {
        "douyin": "https://www.douyin.com/",
        "kuaishou": "https://www.kuaishou.com/",
        "xiaohongshu": "https://www.xiaohongshu.com/",
        "huoshan": "https://www.huoshan.com/",
        "bilibili": "https://www.bilibili.com/",
    }
    homepage = homepage_map.get(platform)
    if not homepage:
        return None

    try:
        session.get(homepage, timeout=10)
    except Exception:
        pass

    cookie_count = len(session.cookies)
    if cookie_count == 0:
        print(f"[cookies] No cookies obtained from {platform}")
        return None

    with open(cookie_file, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for cookie in session.cookies:
            domain = cookie.domain if cookie.domain.startswith(".") else "." + cookie.domain
            secure = "TRUE" if cookie.secure else "FALSE"
            f.write(f"{domain}\tTRUE\t{cookie.path}\t{secure}\t{cookie.expires or 0}\t{cookie.name}\t{cookie.value}\n")

    print(f"[cookies] Got {cookie_count} cookies from {platform} (requests)")
    return cookie_file


def _get_cookies_playwright(url: str, cookie_file: str) -> str | None:
    """用 Playwright 无头浏览器访问页面，获取 JS 生成的 Cookie"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[cookies] Playwright not installed, skipping")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="zh-CN",
            )
            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=20000)
            # 等待 JS 设置 cookie
            page.wait_for_timeout(3000)

            cookies = context.cookies()
            browser.close()

        if not cookies:
            print("[cookies] Playwright: no cookies obtained")
            return None

        with open(cookie_file, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for cookie in cookies:
                domain = cookie.get("domain", "")
                if not domain.startswith("."):
                    domain = "." + domain
                secure = "TRUE" if cookie.get("secure") else "FALSE"
                path = cookie.get("path", "/")
                expires = int(cookie.get("expires", 0))
                name = cookie.get("name", "")
                value = cookie.get("value", "")
                f.write(f"{domain}\tTRUE\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")

        print(f"[cookies] Playwright: got {len(cookies)} cookies")
        return cookie_file
    except Exception as e:
        print(f"[cookies] Playwright error: {e}")
        return None


def _download_video(url: str, output_path: str) -> str | None:
    """使用 yt-dlp 下载视频"""
    import yt_dlp

    # 尝试获取平台 Cookie（抖音等平台需要）
    cookie_file = _get_fresh_cookies(url)

    ydl_opts = {
        "outtmpl": output_path,
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        # 模拟浏览器请求
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.douyin.com/",
        },
    }

    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # yt-dlp 可能加后缀，找实际文件
        for p in Path(output_path).parent.glob("video*"):
            return str(p)
        return None
    except Exception as e:
        print(f"[download error] {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        # 清理 cookie 文件
        if cookie_file:
            try:
                os.unlink(cookie_file)
            except OSError:
                pass


# ════════════════════════════════════════
#  2. 分片上传
# ════════════════════════════════════════

@app.post("/api/upload/init")
async def upload_init(filename: str = Form(...), total_chunks: int = Form(...)):
    """初始化分片上传会话"""
    task_id = create_task("upload")
    task_dir = TASK_DIR / task_id
    chunk_dir = task_dir / "chunks"
    chunk_dir.mkdir(exist_ok=True)

    TASKS[task_id].update({
        "filename": filename,
        "total_chunks": total_chunks,
        "received_chunks": 0,
        "chunk_dir": str(chunk_dir),
    })

    return {"task_id": task_id, "chunk_dir": str(chunk_dir)}


@app.post("/api/upload/chunk")
async def upload_chunk(
    task_id: str = Form(...),
    chunk_index: int = Form(...),
    chunk: UploadFile = File(...),
):
    """上传一个分片"""
    if task_id not in TASKS:
        raise HTTPException(404, "任务不存在")

    task = TASKS[task_id]
    chunk_dir = Path(task["chunk_dir"])
    chunk_path = chunk_dir / f"chunk_{chunk_index:05d}"

    data = await chunk.read()
    async with open(chunk_path, "wb") as f:
        await f.write(data)

    task["received_chunks"] = task.get("received_chunks", 0) + 1
    total = task.get("total_chunks", 1)
    progress = int(task["received_chunks"] / total * 80)  # 上传占 80%
    task["progress"] = min(progress, 80)
    task["message"] = f"上传中 {task['received_chunks']}/{total}"

    return {"received": chunk_index, "progress": task["progress"]}


@app.post("/api/upload/complete")
async def upload_complete(task_id: str = Form(...)):
    """合并分片并提取音频"""
    if task_id not in TASKS:
        raise HTTPException(404, "任务不存在")

    task = TASKS[task_id]
    task_dir = TASK_DIR / task_id
    chunk_dir = Path(task["chunk_dir"])

    # 合并所有分片
    task["status"] = "merging"
    task["message"] = "正在合并视频文件..."
    task["progress"] = 82

    video_path = task_dir / "video.mp4"
    chunks = sorted(chunk_dir.glob("chunk_*"))

    with open(video_path, "wb") as out:
        for chunk_file in chunks:
            with open(chunk_file, "rb") as cf:
                out.write(cf.read())

    # 清理分片
    for chunk_file in chunks:
        chunk_file.unlink()

    task["video_path"] = str(video_path)
    task["status"] = "extracting"
    task["message"] = "正在提取音频..."
    task["progress"] = 90

    # 异步提取音频
    asyncio.create_task(_extract_and_finish(task_id, str(video_path), str(task_dir / "audio.mp3")))

    return {"task_id": task_id, "status": "extracting"}


async def _extract_and_finish(task_id: str, video_path: str, audio_path: str):
    """提取音频并更新任务状态"""
    task = TASKS[task_id]
    try:
        await asyncio.to_thread(_extract_audio, video_path, audio_path)

        task["audio_path"] = audio_path
        task["status"] = "done"
        task["progress"] = 100
        task["message"] = "音频提取完成"
        task["audio_size"] = Path(audio_path).stat().st_size

        # 删除视频文件
        try:
            os.unlink(video_path)
        except OSError:
            pass

    except Exception as e:
        task["status"] = "error"
        task["message"] = f"音频提取失败: {str(e)}"


# ════════════════════════════════════════
#  3. 音频提取核心
# ════════════════════════════════════════

def _extract_audio(video_path: str, audio_path: str):
    """使用 ffmpeg 提取音频为 MP3"""
    import subprocess

    cmd = [
        FFMPEG, "-y",
        "-i", video_path,
        "-vn",                    # 不要视频
        "-acodec", "libmp3lame",  # MP3 编码
        "-ab", "192k",            # 比特率
        "-ar", "44100",           # 采样率
        "-ac", "2",               # 双声道
        "-loglevel", "error",
        audio_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 错误: {result.stderr}")

    if not Path(audio_path).exists():
        raise RuntimeError("音频文件未生成")


# ════════════════════════════════════════
#  4. 任务状态与文件下载
# ════════════════════════════════════════

@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    if task_id not in TASKS:
        raise HTTPException(404, "任务不存在")

    task = TASKS[task_id]
    return {
        "task_id": task_id,
        "status": task["status"],
        "progress": task["progress"],
        "message": task["message"],
        "audio_size": task.get("audio_size", 0),
        "audio_format": task.get("audio_format", "mp3"),
    }


@app.get("/api/audio/{task_id}")
async def download_audio(task_id: str):
    """下载音频文件"""
    if task_id not in TASKS:
        raise HTTPException(404, "任务不存在")

    task = TASKS[task_id]
    if task["status"] != "done" or not task.get("audio_path"):
        raise HTTPException(400, "音频尚未就绪")

    audio_path = task["audio_path"]
    if not Path(audio_path).exists():
        raise HTTPException(404, "音频文件不存在")

    filename = f"audio_{task_id}.mp3"
    return FileResponse(
        audio_path,
        media_type="audio/mpeg",
        filename=filename,
    )


@app.delete("/api/task/{task_id}")
async def delete_task(task_id: str):
    """删除任务及文件"""
    if task_id not in TASKS:
        raise HTTPException(404, "任务不存在")

    task_dir = TASK_DIR / task_id
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)

    del TASKS[task_id]
    return {"deleted": task_id}


@app.get("/api/health")
async def health():
    """健康检查"""
    return {"status": "ok", "ffmpeg": FFMPEG}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
