# 视频提取音频 - 微信小程序

从视频中提取音频，输出 MP3 格式，支持播放和转发到微信聊天。

## 功能

- **微信聊天文件** — 从聊天记录中选择视频
- **相册视频** — 支持大文件分片上传
- **链接解析**（折叠隐藏）— 粘贴抖音、快手、B站、小红书等链接

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | 微信小程序（原生开发，无框架） |
| 后端 | Python + FastAPI + ffmpeg |
| 部署 | 微信云托管（GitHub 自动部署） |
| 通信 | `wx.cloud.callContainer`（微信内网，免域名免备案） |

## 项目结构

```
video_to_audio/
├── backend/                     # Python 后端
│   ├── app.py                   # FastAPI 主应用
│   ├── requirements.txt         # Python 依赖
│   ├── Dockerfile               # 云托管容器镜像
│   ├── uploads/                 # 分片暂存（自动创建）
│   └── tasks/                   # 任务目录（自动创建）
│
├── miniprogram/                 # 微信小程序前端
│   ├── app.js                   # 入口（云托管配置）
│   ├── app.json
│   ├── app.wxss
│   ├── project.config.json      # 开发者工具配置
│   ├── sitemap.json
│   ├── utils/
│   │   └── api.js               # 接口封装（callContainer、分片上传、分块下载）
│   └── pages/
│       ├── index/               # 首页（选择输入方式）
│       ├── processing/          # 处理中（进度显示）
│       └── result/              # 结果页（播放 + 转发）
│
├── DEPLOY_CLOUD.md              # 云托管部署指南
└── README.md
```

## 部署指南

### 后端：微信云托管

1. **创建云托管环境**
   - 打开 [微信云托管控制台](https://cloud.weixin.qq.com/cloudrun)
   - 创建环境，记下环境 ID（类似 `video-extractor-xxxxxxxx`）

2. **创建服务**
   - 服务名：`video-extractor`
   - 代码来源：GitHub 仓库 `tokks/wechat-video-extractor`
   - 构建方式：Dockerfile（路径 `backend/Dockerfile`）
   - 开启「代码更新自动部署」

3. **验证**
   - 部署完成后，服务地址类似 `https://video-extractor-xxx.sh.run.tcloudbase.com`
   - 注意：此地址在浏览器中会显示风险提醒中间页，**小程序通过 callContainer 内网调用不受影响**

### 前端：微信小程序

1. **导入项目**
   - 微信开发者工具 → 导入项目
   - 目录选择 `miniprogram/`
   - AppID 填你自己的

2. **配置云托管环境 ID**
   - 编辑 `app.js`，填入你的 `cloudEnv`：
   ```javascript
   cloudEnv: 'video-extractor-d3fqyqza5900d42e',  // 你的环境 ID
   containerService: 'video-extractor',
   ```

3. **编译测试**
   - 开发者工具中编译运行
   - 选择视频 → 上传 → 等待提取 → 下载音频 → 播放/转发

## 后端接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/parse-link` | POST | 解析短视频链接，返回 task_id |
| `/api/upload/init` | POST | 初始化分片上传 |
| `/api/upload/chunk` | POST | 上传一个分片（base64 + JSON） |
| `/api/upload/complete` | POST | 合并分片并提取音频 |
| `/api/task/{id}` | GET | 查询任务状态 |
| `/api/audio/{id}` | GET | 下载音频（二进制） |
| `/api/audio-base64/{id}` | GET | 下载音频（base64，支持分块） |
| `/api/health` | GET | 健康检查 |

## 上线流程

1. 设置服务类目（工具类）— [mp.weixin.qq.com](https://mp.weixin.qq.com) → 设置 → 基本设置
2. 配置用户隐私保护指引 — 声明使用的 API：照片/视频、文件、剪贴板、文件系统
3. 微信开发者工具点「上传」，版本号 `1.0.0`
4. 管理后台 → 版本管理 → 提交审核
5. 审核通过后点「发布」

## 核心设计

### wx.cloud.callContainer 架构

小程序通过微信内网专线直接调用云托管容器，**不需要公网域名、不需要备案**。

```
小程序 ──(微信内网)──> wx.cloud.callContainer ──> 云托管容器 (video-extractor)
```

### callContainer 限制与对策

| 限制 | 值 | 对策 |
|------|---|------|
| 请求体上限 | 100KB（-606001） | 分片 60KB，base64 后 ~80KB，JSON ~82KB |
| 响应体上限 | 1MB（-606002） | 音频分块下载，每块 800KB base64 |
| data 不支持 ArrayBuffer | — | 转为 base64 通过 JSON body 传输 |
| 需手动 JSON.stringify | — | callContainer 不自动序列化对象 |
| path 不支持 query string | — | 参数放 JSON body（后端兼容 query params fallback） |

### 分片上传流程

```
选视频 → fs.readFile(position, length) 读取 60KB 分片
      → wx.arrayBufferToBase64 转 base64
      → callContainer JSON body {task_id, chunk_index, chunk_data}
      → 逐片上传直到完成
      → /api/upload/complete 合并 + ffmpeg 提取
```

### 音频分块下载流程

```
callContainer 获取音频大小
  → 逐块请求 /api/audio-base64/{id}?offset=X&length=800000
  → 收集所有 base64 块
  → wx.base64ToArrayBuffer 合并
  → fs.writeFile 保存到 USER_DATA_PATH
  → 本地播放 / wx.shareFileMessage 转发
```

### 音频输出

- 格式：MP3
- 比特率：192kbps
- 采样率：44100Hz
- 声道：双声道

---

## 开发踩坑记录

本项目开发过程中遇到了大量微信小程序和云托管的坑，以下按时间线记录：

### 1. 后端：抖音视频下载超时

- **问题**：云服务器带宽 ~156KB/s，30 秒超时只下载了 4.7MB/35MB
- **修复**：改用 `stream=True` 流式下载，超时放宽到 `(10, 600)` 秒

### 2. 后端：快手链接 yt-dlp 不支持

- **问题**：`yt-dlp` 新版移除了快手提取器，返回 `Unsupported URL`
- **探索**：第三方接口成功率仅 ~30%；桌面 UA 抓取被反爬挡死
- **修复**：用**移动端 UA + curl_cffi** 直接抓快手页面，从 `window.INIT_STATE` 提取 CDN 地址，成功率 100%

### 3. 后端：快手短链接重定向到内部域名

- **问题**：`v.kuaishou.com` 短链接 302 重定向到 `chenzhongtech.com`（快手内部域名），不是 `www.kuaishou.com`
- **修复**：不按域名判断，从重定向 URL 的 query params / path 中提取 photoId 后构造标准 URL

### 4. 前端：本地视频上传一直转圈

- **问题**：后端 `async with open()` 不支持异步（内置 open 返回的文件对象不支持 async with），分片实际没写入；前端 `getCurrentPages()` 获取页面实例有时序问题
- **修复**：后端改用 `aiofiles.open`；前端改用 `app.globalData` 传递上传状态，processing 页轮询读取

### 5. 前端：分片根本没上传

- **问题**：`wx.uploadFile` 不支持 offset/range，传的是整个文件而非分片；success 回调不检查 statusCode，4xx 也当成功
- **修复**：`fs.readFile({position, length})` 读取分片 → 转 base64 → callContainer 上传；检查 `res.statusCode`

### 6. 真机：`wx.chooseMessageFile` 选不到视频

- **问题**：`type: 'file'` 只显示非图片/视频文件，视频属于 video 类型
- **修复**：改为 `type: 'all'` 显示所有类型

### 7. 真机：云托管默认域名返回风险提醒中间页

- **问题**：`xxx.sh.run.tcloudbase.com` 返回 CloudBase「风险提醒」HTML 中间页，小程序 `wx.request` 无法自动通过
- **修复**：改用 `wx.cloud.callContainer` 走微信内网，彻底绕过公网域名

### 8. callContainer：JSON 不自动序列化

- **问题**：callContainer 的 `data` 传对象时不会自动 `JSON.stringify`，后端收到 `[object Object]`
- **修复**：手动 `JSON.stringify(data)` 再传

### 9. callContainer：不支持 ArrayBuffer

- **问题**：callContainer 的 `data` 参数不支持 ArrayBuffer 二进制直传
- **修复**：用 `wx.arrayBufferToBase64()` 转 base64，通过 JSON body 传输

### 10. callContainer：-606001 请求体超限

- **问题**：分片 2MB → base64 后 ~2.7MB，远超 callContainer 请求体限制
- **发现**：callContainer 请求体限制是 **100KB**（不是文档暗示的更大值）
- **修复**：分片缩小到 60KB（base64 后 ~80KB，JSON body ~82KB，安全在 100KB 内）

### 11. callContainer：-606002 响应体超限

- **问题**：音频文件 > 1MB，callContainer 响应体限制 1MB，一次性返回超限
- **修复**：后端 `/api/audio-base64` 增加 `offset`/`length` 参数支持分块返回；前端逐块下载 800KB base64 后合并

### 12. 上线：链接解析审核风险

- **问题**：链接解析功能涉及第三方平台内容抓取，可能被审核打回
- **修复**：首页默认只显示本地视频功能，链接解析放在「更多方式」折叠区域，点击展开。审核不通过则改 `showLinkParse` 默认值即可彻底屏蔽

---

## 开发路线图

- [ ] 支持选择音频输出格式（WAV/AAC/FLAC）
- [ ] 支持截取音频片段（指定时间段）
- [ ] 接入云存储替代内存存储
- [ ] 添加提取历史记录

## License

MIT
