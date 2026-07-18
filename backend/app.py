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
import urllib.parse
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


def _save_cookies_netscape(cookie_jar, cookie_file: str, source: str):
    """保存 Cookie 为 Netscape 格式，兼容 curl_cffi 和 requests"""
    # curl_cffi: session.cookies.jar → http.cookiejar.Cookie 对象列表
    # requests: session.cookies → RequestsCookieJar（继承 CookieJar），迭代得到 Cookie 对象
    if hasattr(cookie_jar, "jar"):
        cookies = list(cookie_jar.jar)
    else:
        cookies = list(cookie_jar)

    if not cookies:
        print(f"[cookies] No cookies from {source}")
        return False

    count = 0
    with open(cookie_file, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        for cookie in cookies:
            # 跳过字符串（curl_cffi 直接迭代 cookies 会返回 cookie 名字符串）
            if isinstance(cookie, str):
                continue
            # http.cookiejar.Cookie 对象
            domain = getattr(cookie, "domain", "") or ""
            if not domain:
                continue
            if not domain.startswith("."):
                domain = "." + domain
            name = getattr(cookie, "name", "") or ""
            value = getattr(cookie, "value", "") or ""
            path = getattr(cookie, "path", "/") or "/"
            secure = getattr(cookie, "secure", False)
            expires = getattr(cookie, "expires", 0) or 0
            secure_str = "TRUE" if secure else "FALSE"
            f.write(f"{domain}\tTRUE\t{path}\t{secure_str}\t{expires}\t{name}\t{value}\n")
            count += 1

    if count == 0:
        print(f"[cookies] No valid cookies from {source}")
        return False
    print(f"[cookies] Got {count} cookies from {source}")
    return True


def _get_fresh_cookies(url: str) -> str | None:
    """获取平台 Cookie，用 curl_cffi 模拟 Chrome TLS 指纹"""
    platform = detect_platform(url)
    cookie_file = str(Path(tempfile.gettempdir()) / f"cookies_{platform}.txt")

    # ── 方案1: curl_cffi 模拟 Chrome TLS 指纹获取 Cookie ──
    try:
        from curl_cffi import requests as cffi_requests

        session = cffi_requests.Session(impersonate="chrome120")

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

        # 访问首页，让 JS 设置 Cookie（ttwid 等会在 Set-Cookie 响应头返回）
        try:
            resp = session.get(homepage, timeout=15)
            print(f"[cookies] {platform} homepage status={resp.status_code}")
        except Exception as e:
            print(f"[cookies] {platform} homepage failed: {e}")

        # 抖音额外访问分享链接，获取更多 Cookie
        if platform == "douyin":
            try:
                session.get(url, timeout=15, allow_redirects=True)
            except Exception:
                pass

        # 关键修复：用 .jar 而不是直接迭代 session.cookies
        if _save_cookies_netscape(session.cookies, cookie_file, f"{platform} (curl_cffi)"):
            return cookie_file

    except ImportError:
        print("[cookies] curl_cffi not installed")
    except Exception as e:
        print(f"[cookies] curl_cffi error: {e}")

    # ── 方案2: requests 兜底 ──
    import requests as req_lib

    session = req_lib.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })

    try:
        session.get(homepage or "https://www.douyin.com/", timeout=10)
    except Exception:
        pass

    if _save_cookies_netscape(session.cookies, cookie_file, f"{platform} (requests)"):
        return cookie_file
    return None


def _find_json_key(data, key):
    """递归在 JSON/dict/list 中查找指定 key 的第一个匹配值"""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for v in data.values():
            result = _find_json_key(v, key)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_json_key(item, key)
            if result is not None:
                return result
    return None


def _find_item_list(data):
    """从 _ROUTER_DATA 提取 item_list（标准路径优先，回退到任意 list 值）"""
    # 标准路径: loaderData[<page>]['videoInfoRes']['item_list']
    video_info = _find_json_key(data, "videoInfoRes")
    if isinstance(video_info, dict) and isinstance(video_info.get("item_list"), list):
        return video_info["item_list"]
    item_list = _find_json_key(data, "item_list")
    if isinstance(item_list, list):
        return item_list
    return None


def _download_douyin_direct(url: str, output_path: str) -> str | None:
    """抖音直接下载 - 移动端分享页(window._ROUTER_DATA)解析，无需 Cookie"""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("[douyin-direct] curl_cffi not installed")
        return None

    try:
        session = cffi_requests.Session(impersonate="chrome120")
        # 移动端 UA + Referer（抖音反爬校验，缺少会 403）
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.0; SM-G955U Build/R16NW) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Mobile Safari/537.36",
            "Referer": "https://www.douyin.com/?is_from_mobile_home=1&recommend=1",
        })

        # Step 1: 从分享短链跟随重定向，获取视频 ID
        print(f"[douyin-direct] Following share link: {url}")
        resp = session.get(url, allow_redirects=True, timeout=15)
        final_url = str(resp.url)

        video_id = None
        match = re.search(r"/video/(\d+)", final_url) or re.search(r"/video/(\d+)", resp.text)
        if match:
            video_id = match.group(1)
        else:
            match = re.search(r"awemeId[\"\s:]+[\"\']?(\d{15,})", resp.text)
            if match:
                video_id = match.group(1)

        if not video_id:
            print(f"[douyin-direct] Cannot find video ID from {final_url}")
            return None
        print(f"[douyin-direct] Video ID: {video_id}")

        # Step 2: 访问移动端分享页，提取 window._ROUTER_DATA
        share_url = f"https://www.iesdouyin.com/share/video/{video_id}/"
        print(f"[douyin-direct] Fetching share page: {share_url}")
        resp = session.get(share_url, timeout=15)
        print(f"[douyin-direct] Share page status={resp.status_code}")

        if resp.status_code != 200:
            print(f"[douyin-direct] Share page failed")
            return None

        # 提取 window._ROUTER_DATA = {...}; （非贪婪匹配到 </script>）
        match = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\});?\s*</script>", resp.text, re.DOTALL)
        if not match:
            print(f"[douyin-direct] No _ROUTER_DATA found in share page")
            return None

        try:
            router_data = json.loads(match.group(1))
        except Exception as e:
            print(f"[douyin-direct] JSON parse error: {e}")
            return None

        # 递归查找 item_list（路径: loaderData['video_(id)/page']['videoInfoRes']['item_list']）
        item_list = _find_item_list(router_data)
        if not item_list:
            print(f"[douyin-direct] No item_list in _ROUTER_DATA")
            return None

        item = item_list[0] if isinstance(item_list, list) else None
        if not isinstance(item, dict):
            print(f"[douyin-direct] item_list[0] is not a dict, skip")
            return None
        video = item.get("video") or {}
        if not isinstance(video, dict):
            print(f"[douyin-direct] item.video is not a dict, skip")
            return None
        # play_addr 优先，download_addr 兜底
        play_addr = video.get("play_addr") or video.get("download_addr") or {}
        if not isinstance(play_addr, dict):
            print(f"[douyin-direct] no valid play_addr in item")
            return None
        video_uri = play_addr.get("uri")

        if not video_uri:
            print(f"[douyin-direct] No video uri in play_addr")
            return None
        print(f"[douyin-direct] Video URI: {video_uri}")

        # Step 3: 请求播放接口，跟随重定向拿到真实 CDN 地址
        # 关键: 用 stream=True 只取最终 URL，不下载 body
        # （否则云环境带宽慢时，35MB body 在 30s 内下不完会触发 curl 超时）
        play_url = f"https://www.douyin.com/aweme/v1/play/?video_id={video_uri}"
        print(f"[douyin-direct] Requesting play URL...")
        resp = session.get(play_url, timeout=(10, 30), allow_redirects=True, stream=True)
        video_url = str(resp.url)
        resp.close()  # 释放连接，不读取 body
        print(f"[douyin-direct] Final video URL: {video_url[:120]}")

        # Step 4: 下载视频（流式 + 宽松读超时，避免云环境带宽慢导致整体超时）
        # 读超时给 600s：云环境 ~156KB/s 下 35MB 约需 228s，留出充足余量
        print(f"[douyin-direct] Downloading video...")
        video_resp = session.get(video_url, timeout=(10, 600), stream=True)
        if video_resp.status_code == 200:
            downloaded = 0
            with open(output_path, "wb") as f:
                for chunk in video_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            video_resp.close()
            if downloaded > 1000:
                print(f"[douyin-direct] Downloaded {downloaded} bytes")
                return output_path
            else:
                print(f"[douyin-direct] Download too small: {downloaded} bytes")
                return None
        else:
            print(f"[douyin-direct] Download failed: status={video_resp.status_code}")
            return None

    except Exception as e:
        print(f"[douyin-direct] Error: {e}")
        import traceback
        traceback.print_exc()
        return None


def _download_douyin_thirdparty(url: str, output_path: str) -> str | None:
    """兜底：第三方解析 API 获取无水印视频地址（无需 Cookie）"""
    import requests as req_lib

    api = f"https://api.yujn.cn/api/dy_jx.php?msg={urllib.parse.quote(url, safe='')}"
    try:
        print(f"[thirdparty] Trying API: {api[:70]}...")
        resp = req_lib.get(api, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
        })
        if resp.status_code != 200:
            print(f"[thirdparty] API status={resp.status_code}")
            return None
        data = resp.json()
        # 兼容多种返回格式
        video_url = None
        if isinstance(data, dict):
            video_url = (
                data.get("video_url") or data.get("url") or
                data.get("play_url") or data.get("downurl") or
                (data.get("data", {}).get("url") if isinstance(data.get("data"), dict) else None) or
                (data.get("data") if isinstance(data.get("data"), str) else None)
            )
        if not video_url:
            print(f"[thirdparty] No video URL in response: {str(data)[:200]}")
            return None
        print(f"[thirdparty] Got video URL, downloading...")
        video_resp = req_lib.get(video_url, timeout=60, stream=True)
        if video_resp.status_code == 200:
            with open(output_path, "wb") as f:
                for chunk in video_resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            if os.path.getsize(output_path) > 1000:
                print(f"[thirdparty] Downloaded {os.path.getsize(output_path)} bytes")
                return output_path
        print(f"[thirdparty] Download failed: status={video_resp.status_code}")
    except Exception as e:
        print(f"[thirdparty] Error: {e}")
    return None


def _download_kuaishou_direct(url: str, output_path: str) -> str | None:
    """快手：移动端UA直接抓取页面，从INIT_STATE提取视频地址（无需Cookie/第三方接口）

    关键发现：必须用移动端UA(iPhone)，桌面UA只返回SPA空壳不含视频数据。
    移动端页面内嵌 window.INIT_STATE，含 mainMvUrls 视频直链。
    首次即成功，不依赖第三方脆接口。
    """
    import re
    import requests as req_lib
    from curl_cffi import requests as cffi_req

    # 剥掉分享参数，提取 photoId 和类型
    type_m = re.search(r'kuaishou\.com/(short-video|long-video|photo)/([a-zA-Z0-9_-]+)', url)
    if not type_m:
        print(f"[ks-direct] Cannot extract photo ID from URL: {url}")
        return None
    content_type, photo_id = type_m.group(1), type_m.group(2)
    page_url = f"https://www.kuaishou.com/{content_type}/{photo_id}"

    MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                 "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
                 "Mobile/15E148 Safari/604.1 Edg/122.0.0.0")

    print(f"[ks-direct] Fetching {page_url} (mobile UA)...")
    try:
        s = cffi_req.Session(impersonate="chrome")
        resp = s.get(page_url, headers={
            "User-Agent": MOBILE_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }, timeout=20)
        html = resp.text
        if len(html) < 5000:
            print(f"[ks-direct] Page too small ({len(html)} bytes), likely blocked")
            return None
        print(f"[ks-direct] Got page, {len(html)} bytes")
    except Exception as e:
        print(f"[ks-direct] Fetch err: {e}")
        return None

    # 提取视频地址：优先 mainMvUrls，其次 photoUrl，再次 manifest backupUrl
    video_url = None

    mv_match = re.search(r'"mainMvUrls"\s*:\s*\[\s*\{[^}]*"url"\s*:\s*"(https?://[^"]+)"', html)
    if mv_match:
        video_url = mv_match.group(1)
        print("[ks-direct] Found mainMvUrls URL")

    if not video_url:
        pu_match = re.search(r'"photoUrl"\s*:\s*"(https?://[^"]+)"', html)
        if pu_match:
            video_url = pu_match.group(1)
            print("[ks-direct] Found photoUrl")

    if not video_url:
        mb_match = re.search(r'"backupUrl"\s*:\s*\[\s*"(https?://[^"]+\.mp4[^"]*)"', html)
        if mb_match:
            video_url = mb_match.group(1)
            print("[ks-direct] Found manifest backupUrl")

    if not video_url:
        print("[ks-direct] No video URL found in page")
        return None

    # 处理可能的 unicode 转义
    video_url = video_url.replace("\\u002F", "/").replace("\\/", "/")

    # 下载视频
    print(f"[ks-direct] Downloading video...")
    try:
        video_resp = req_lib.get(
            video_url, timeout=60, stream=True,
            headers={"User-Agent": MOBILE_UA, "Referer": "https://www.kuaishou.com/"})
        if video_resp.status_code == 200:
            downloaded = 0
            with open(output_path, "wb") as f:
                for chunk in video_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            if downloaded > 1000:
                print(f"[ks-direct] Downloaded {downloaded} bytes")
                return output_path
            else:
                print(f"[ks-direct] Download too small: {downloaded} bytes")
                return None
        else:
            print(f"[ks-direct] Download failed: status={video_resp.status_code}")
    except Exception as e:
        print(f"[ks-direct] Download err: {e}")
    return None


def _download_kuaishou_thirdparty(url: str, output_path: str) -> str | None:
    """快手：第三方解析接口获取无水印视频地址（兜底，直接抓取失败时用）

    直接抓取(移动端UA)是主方案；此函数仅在直接抓取失败时兜底。
    api.yujn.cn 接口偶发超时/返回null，加重试+null容错。
    """
    import time
    import requests as req_lib

    api = f"https://api.yujn.cn/api/kuaishou.php?url={urllib.parse.quote(url, safe='')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    }
    video_url = None
    for attempt in range(1, 4):
        try:
            print(f"[ks-thirdparty] API try {attempt}...")
            resp = req_lib.get(api, timeout=35, headers=headers)
            data = resp.json() if resp.text.strip() else None
            if isinstance(data, dict) and data.get("code") == 200:
                d = data.get("data")
                video_url = d.get("url") if isinstance(d, dict) else data.get("url")
                if video_url:
                    break
            elif isinstance(data, dict):
                print(f"[ks-thirdparty] API code={data.get('code')} msg={data.get('msg')}")
            else:
                print(f"[ks-thirdparty] API returned null/non-JSON")
        except Exception as e:
            print(f"[ks-thirdparty] API err (attempt {attempt}): {e}")
        time.sleep(2)

    if not video_url:
        print("[ks-thirdparty] No video URL obtained")
        return None

    print(f"[ks-thirdparty] Got video URL, downloading...")
    try:
        video_resp = req_lib.get(
            video_url, timeout=60, stream=True,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.kuaishou.com/"})
        if video_resp.status_code == 200:
            downloaded = 0
            with open(output_path, "wb") as f:
                for chunk in video_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
            if downloaded > 1000:
                print(f"[ks-thirdparty] Downloaded {downloaded} bytes")
                return output_path
            else:
                print(f"[ks-thirdparty] Download too small: {downloaded} bytes")
                return None
        else:
            print(f"[ks-thirdparty] Download failed: status={video_resp.status_code}")
    except Exception as e:
        print(f"[ks-thirdparty] Download err: {e}")
    return None


def _download_video(url: str, output_path: str) -> str | None:
    """下载视频：抖音优先直接下载，其他用 yt-dlp"""
    platform = detect_platform(url)

    # 抖音优先尝试直接下载（无需 Cookie）；失败再用第三方 API 兜底
    if platform == "douyin":
        print("[download] Trying direct Douyin download (no cookie needed)...")
        result = _download_douyin_direct(url, output_path)
        if result:
            return result
        print("[download] Direct download failed, trying third-party API...")
        result = _download_douyin_thirdparty(url, output_path)
        if result:
            return result
        print("[download] All Douyin methods failed")
        return None

    # 快手：优先直接抓取(移动端UA)，失败用第三方接口兜底
    if platform == "kuaishou":
        # 如果是短链接(v.kuaishou.com / gifshow.com)，先跟随重定向拿到真实URL
        if "v.kuaishou.com" in url or "gifshow.com" in url:
            try:
                print("[download] Resolving Kuaishou short link...")
                import requests as _req
                _r = _req.head(url, allow_redirects=True, timeout=10, headers={
                    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
                                  "Mobile/15E148 Safari/604.1"})
                if _r.url and "kuaishou.com" in _r.url:
                    print(f"[download] Resolved: {url} -> {_r.url}")
                    url = _r.url
                else:
                    print(f"[download] Resolve returned unexpected URL: {_r.url}")
            except Exception as e:
                print(f"[download] Resolve short link err: {e}")
        print("[download] Trying Kuaishou direct download (mobile UA)...")
        result = _download_kuaishou_direct(url, output_path)
        if result:
            return result
        print("[download] Kuaishou direct failed, trying third-party API...")
        result = _download_kuaishou_thirdparty(url, output_path)
        if result:
            return result
        print("[download] All Kuaishou methods failed")
        return None

    # yt-dlp 下载
    import yt_dlp

    cookie_file = _get_fresh_cookies(url)

    # Referer 必须按平台设置，否则 B站/小红书等会被拒
    _referer_map = {
        "douyin": "https://www.douyin.com/",
        "kuaishou": "https://www.kuaishou.com/",
        "bilibili": "https://www.bilibili.com/",
        "xiaohongshu": "https://www.xiaohongshu.com/",
        "huoshan": "https://www.huoshan.com/",
    }
    ydl_opts = {
        "outtmpl": output_path,
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": _referer_map.get(platform, "https://www.douyin.com/"),
        },
    }

    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        for p in Path(output_path).parent.glob("video*"):
            return str(p)
        return None
    except Exception as e:
        print(f"[download error] {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
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
