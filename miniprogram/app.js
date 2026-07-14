// app.js - 小程序入口
App({
  globalData: {
    // ★★★ 部署后改为你的服务器地址（必须是 HTTPS） ★★★
    baseUrl: 'http://localhost:8000',
    // 分片大小：2MB（微信单次上传限制 10MB，留余量）
    chunkSize: 2 * 1024 * 1024,
  },

  onLaunch() {
    console.log('视频提取音频小程序启动');
  },
});
