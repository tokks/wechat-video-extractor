// app.js - 小程序入口
App({
  globalData: {
    // ── 微信云托管配置 ──
    // ⚠️ 使用 wx.cloud.callContainer 方式调用后端，不需要公网域名、不需要备案。
    //
    // 获取方式：
    // 1. 打开 https://cloud.weixin.qq.com/cloudrun 微信云托管控制台
    // 2. 左上角能看到「环境」下拉，点开后会显示环境 ID（类似 prod-xxxxx 或 video-extractor-xxxxx）
    // 3. 把下面的 cloudEnv 替换成你的环境 ID

    cloudEnv: 'video-extractor-d3fqyqza5900d42e',           // ← 改成你的云托管环境 ID
    containerService: 'video-extractor', // 云托管服务名

    // 旧版公网域名（仅作参考，callContainer 模式下不需要）
    // baseUrl: 'https://video-extractor-283096-9-1454775300.sh.run.tcloudbase.com',

    // 分片大小：60KB
    // callContainer 请求体限制 100KB，base64 膨胀 33%，60KB → ~80KB base64 → JSON ~82KB（安全余量）
    chunkSize: 60 * 1024,
  },

  onLaunch() {
    console.log('视频提取音频小程序启动');

    // 初始化云托管 SDK
    if (wx.cloud) {
      wx.cloud.init({
        env: this.globalData.cloudEnv,
        traceUser: true,
      });
      console.log('[cloud] wx.cloud.init 成功, env:', this.globalData.cloudEnv);
    } else {
      console.error('[cloud] 当前基础库版本不支持 wx.cloud，请升级微信开发者工具');
    }
  },
});
