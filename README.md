# 视频提取音频 - 微信小程序

从小程序中提取视频音频，支持三种视频来源：
1. **短视频链接** — 抖音、快手、火山、B站、小红书
2. **微信聊天文件** — 从聊天记录中选择视频
3. **相册视频** — 支持大于 1GB 的大文件

---

## 项目结构

```
video_to_audio/
├── backend/                 # Python 后端服务
│   ├── app.py               # FastAPI 主应用（所有接口）
│   ├── requirements.txt     # Python 依赖
│   ├── uploads/             # 分片暂存目录（自动创建）
│   └── tasks/               # 任务目录（自动创建）
│
├── miniprogram/             # 微信小程序前端
│   ├── app.js               # 入口（baseUrl 在这里配置）
│   ├── app.json
│   ├── app.wxss             # 全局样式
│   ├── project.config.json  # 开发者工具配置
│   ├── sitemap.json
│   ├── utils/
│   │   └── api.js           # 接口封装（链接解析、分片上传、轮询）
│   └── pages/
│       ├── index/           # 首页（选择输入方式）
│       ├── processing/      # 处理中（进度显示）
│       └── result/          # 结果页（播放+下载）
│
└── README.md
```

---

## 后端部署

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python app.py
```

默认监听 `http://0.0.0.0:8000`

### 3. 验证

```bash
curl http://localhost:8000/api/health
# {"status":"ok","ffmpeg":"ffmpeg"}
```

### 后端接口一览

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/parse-link` | POST | 提交短视频链接，返回 task_id |
| `/api/upload/init` | POST | 初始化分片上传 |
| `/api/upload/chunk` | POST | 上传一个分片 |
| `/api/upload/complete` | POST | 合并分片并提取音频 |
| `/api/task/{id}` | GET | 查询任务状态 |
| `/api/audio/{id}` | GET | 下载音频文件 |
| `/api/task/{id}` | DELETE | 删除任务 |
| `/api/health` | GET | 健康检查 |

### 生产环境注意事项

- 需要安装系统级 `ffmpeg`，或依赖 `imageio-ffmpeg` 自带的（已含在依赖中）
- 任务状态存储在内存中，重启丢失。生产环境建议换 Redis
- `yt-dlp` 需要定期更新以适配各平台链接变化：`pip install -U yt-dlp`
- 服务器需要 HTTPS（微信小程序要求）
- 建议用 `nginx` 反向代理 + `gunicorn`/`uvicorn` 多进程

---

## 小程序部署

### 1. 导入项目

1. 打开 [微信开发者工具](https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html)
2. 选择「导入项目」
3. 目录选择 `miniprogram/` 文件夹
4. AppID 填你自己的（或用测试号）

### 2. 配置服务器地址

编辑 `miniprogram/app.js`，将 `baseUrl` 改为你的服务器地址：

```javascript
globalData: {
  baseUrl: 'https://your-domain.com',
}
```

### 3. 微信后台配置

1. 登录 [微信公众平台](https://mp.weixin.qq.com)
2. 开发管理 → 开发设置 → 服务器域名
3. 在 `request合法域名` 和 `downloadFile合法域名` 中添加你的服务器域名

### 4. 本地开发调试

在开发者工具中勾选「详情 → 本地设置 → 不校验合法域名」即可用 `http://localhost:8000` 调试。

---

## 核心设计说明

### 分片上传（支持大文件）

微信小程序 `wx.uploadFile` 单次上传限制 10MB。对于大于 1GB 的视频：

1. 前端将文件按 **2MB** 一片切分
2. 逐片上传到 `/api/upload/chunk`
3. 全部上传后调用 `/api/upload/complete` 合并
4. 后端用 ffmpeg 提取音频

### 短视频链接解析

使用 `yt-dlp` 库，自动识别平台并下载无水印视频：

- 抖音：`v.douyin.com` / `douyin.com`
- 快手：`v.kuaishou.com` / `kuaishou.com`
- 火山：`huoshan.com`
- B站：`bilibili.com` / `b23.tv`
- 小红书：`xiaohongshu.com` / `xhslink.com`

### 音频输出

- 格式：MP3
- 比特率：192kbps
- 采样率：44100Hz
- 声道：双声道

---

## 开发路线图

- [ ] 支持选择音频输出格式（WAV/AAC/FLAC）
- [ ] 支持截取音频片段（指定时间段）
- [ ] 支持批量链接处理
- [ ] 接入云存储（七牛/OSS）替代本地存储
- [ ] 添加用户登录和提取历史记录
