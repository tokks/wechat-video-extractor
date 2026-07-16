# 部署指南

本指南分三部分：**GitHub 上传** → **Render 部署** → **小程序配置**。

---

## 第一部分：上传到 GitHub

### 1. 在 GitHub 网页创建仓库

1. 打开 [github.com](https://github.com)，登录你的账号
2. 点击右上角 **`+`** → **New repository**
3. 填写信息：
   - Repository name: `wechat-video-extractor`
   - Description: `视频提取音频微信小程序`
   - 选择 **Public**（公开）
   - **不要**勾选 "Add a README file"
   - **不要**勾选 ".gitignore" 和 "license"
4. 点击 **Create repository**

### 2. 本地推送代码

项目已经初始化了 Git 并提交了代码。打开 PowerShell：

```powershell
# 进入项目目录
cd "c:\Users\97541\WorkBuddy\20260714202958\video_to_audio"

# 添加远程仓库（把 YOUR_USERNAME 换成你的 GitHub 用户名）
git remote add origin https://github.com/YOUR_USERNAME/wechat-video-extractor.git

# 推送到 GitHub
git branch -M main
git push -u origin main
```

> 如果提示输入密码，GitHub 已不支持账号密码，需要用 Personal Access Token：
> 1. 打开 https://github.com/settings/tokens
> 2. Generate new token (classic) → 勾选 `repo` 权限
> 3. 生成后复制 token，作为密码粘贴

推送成功后，GitHub 上就能看到代码了。

---

## 第二部分：部署到 Render

### 1. 注册 Render

1. 打开 [render.com](https://render.com)
2. 点击 **Sign Up** → 选 **GitHub** 登录
3. 授权 Render 访问你的 GitHub 仓库

### 2. 创建 Web Service

**方式 A：用 Blueprint（推荐）**

1. 在 Render Dashboard 点击 **New** → **Blueprint**
2. 选择你的 `wechat-video-extractor` 仓库
3. Render 会自动识别 `render.yaml` 配置文件
4. 点击 **Apply** 或 **Create**

**方式 B：手动创建**

1. Render Dashboard → **New** → **Web Service**
2. 选择 `wechat-video-extractor` 仓库
3. 填写配置：

| 配置项 | 值 |
|--------|-----|
| Name | `wechat-video-extractor` |
| Runtime | **Docker** |
| Dockerfile Path | `./backend/Dockerfile` |
| Docker Context | `backend` |
| Instance Type | **Free** |

4. 点击 **Create Web Service**

### 3. 等待部署

- Render 会自动拉取代码、构建 Docker 镜像（装 Python + ffmpeg + 依赖）
- 构建过程约 3-5 分钟
- 构建完成后，Render 会给你一个域名，格式类似：
  ```
  https://wechat-video-extractor.onrender.com
  ```

### 4. 验证部署

浏览器打开：
```
https://wechat-video-extractor.onrender.com/api/health
```

返回 `{"status":"ok","ffmpeg":"ffmpeg"}` 即部署成功。

### 5. Render 免费版注意事项

| 限制 | 说明 |
|------|------|
| 休眠 | 15 分钟无请求会自动休眠，下次请求冷启动约 30-60 秒 |
| 运行时间 | 每月 750 小时免费额度 |
| 磁盘 | 临时存储，重启后 uploads/tasks 目录会被清空 |
| 端口 | Render 自动分配端口，通过 `PORT` 环境变量传入，代码已适配 |

> 如果需要持久化存储，在 Render 添加 Disk（收费）或接入云存储（七牛/OSS）。

---

## 第三部分：小程序配置

### 1. 修改服务器地址

编辑 `miniprogram/app.js`，把 `baseUrl` 改成 Render 分配的域名：

```javascript
globalData: {
  baseUrl: 'https://wechat-video-extractor.onrender.com',
  chunkSize: 2 * 1024 * 1024,
}
```

### 2. 微信开发者工具调试

1. 打开 [微信开发者工具](https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html)
2. 导入项目 → 选择 `miniprogram/` 文件夹
3. 填写你的 AppID（没有就用测试号）
4. **详情 → 本地设置 → 勾选「不校验合法域名」**

> 勾选后可以直接用 Render 的域名调试，不需要微信后台配置。

### 3. 正式上线配置（发布时需要）

1. 登录 [微信公众平台](https://mp.weixin.qq.com)
2. **开发管理 → 开发设置 → 服务器域名**
3. 在 **request 合法域名** 添加：
   ```
   https://wechat-video-extractor.onrender.com
   ```
4. 在 **downloadFile 合法域名** 添加：
   ```
   https://wechat-video-extractor.onrender.com
   ```
5. 保存后重新编译小程序即可

---

## 完整流程检查清单

- [ ] GitHub 仓库已创建并推送代码
- [ ] Render Web Service 已创建并构建成功
- [ ] `/api/health` 返回正常
- [ ] 小程序 `app.js` 的 `baseUrl` 已改为 Render 域名
- [ ] 微信开发者工具能正常调用接口
- [ ] （上线时）微信后台已配置合法域名

---

## 常见问题

**Q: git push 提示认证失败？**

用 Personal Access Token 替代密码：https://github.com/settings/tokens → 生成 token（勾选 repo 权限）→ 推送时作为密码输入。

**Q: Render 构建失败？**

检查 Dockerfile 路径。Render 配置中 Dockerfile Path 应为 `./backend/Dockerfile`，Docker Context 应为 `backend`。

**Q: 小程序请求报错「不在合法域名列表中」？**

开发阶段勾选「不校验合法域名」即可。正式发布需在微信后台添加域名。

**Q: Render 休眠后请求很慢？**

免费版 15 分钟无请求会休眠。可在 Render 设置 Health Check 路径 `/api/health`，减少冷启动等待。或升级付费计划。

**Q: yt-dlp 解析失败？**

各平台更新频繁，定期更新 yt-dlp：`pip install -U yt-dlp`。Render 上可在 Dockerfile 中添加 `RUN pip install --no-cache-dir -U yt-dlp`。

**Q: 大文件上传中途断了？**

前端分片上传有重试机制（每片最多重试 3 次）。断了可以重新上传，后端会覆盖同名分片。
