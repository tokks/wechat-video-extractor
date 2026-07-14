// pages/index/index.js - 首页
const api = require('../../utils/api.js');

Page({
  data: {
    linkInput: '',
    supportedPlatforms: ['抖音', '快手', '火山', 'B站', '小红书'],
  },

  // ── 链接输入 ──
  onLinkInput(e) {
    this.setData({ linkInput: e.detail.value });
  },

  onPasteLink() {
    wx.getClipboardData({
      success: (res) => {
        this.setData({ linkInput: res.data });
        wx.showToast({ title: '已粘贴', icon: 'success' });
      },
    });
  },

  onClearLink() {
    this.setData({ linkInput: '' });
  },

  // ── 链接解析 ──
  onParseLink() {
    const url = this.data.linkInput.trim();
    if (!url) {
      wx.showToast({ title: '请输入链接', icon: 'none' });
      return;
    }

    wx.showLoading({ title: '解析中...' });
    api.parseLink(url).then((res) => {
      wx.hideLoading();
      wx.navigateTo({
        url: '/pages/processing/processing?taskId=' + res.task_id +
             '&platform=' + res.platform +
             '&type=link',
      });
    }).catch((err) => {
      wx.hideLoading();
      wx.showModal({
        title: '解析失败',
        content: err.message || '请检查链接是否正确',
        showCancel: false,
      });
    });
  },

  // ── 从微信聊天选择文件 ──
  onChooseFromChat() {
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['mp4', 'mov', 'avi', 'mkv', 'flv', 'wmv', 'm4v', 'ts'],
      success: (res) => {
        const file = res.tempFiles[0];
        console.log('[choose chat file]', file.name, file.size);
        this._startUpload(file.path, file.name);
      },
      fail: () => {
        wx.showToast({ title: '已取消', icon: 'none' });
      },
    });
  },

  // ── 从相册选择视频 ──
  onChooseFromAlbum() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['video'],
      sourceType: ['album'],
      maxDuration: 60 * 60,
      camera: 'back',
      success: (res) => {
        const file = res.tempFiles[0];
        console.log('[choose album file]', file);
        const filename = 'video_' + Date.now() + '.mp4';
        this._startUpload(file.tempFilePath, filename);
      },
      fail: () => {
        wx.showToast({ title: '已取消', icon: 'none' });
      },
    });
  },

  // ── 开始分片上传 ──
  _startUpload(filePath, filename) {
    wx.navigateTo({
      url: '/pages/processing/processing?type=upload&filename=' + encodeURIComponent(filename),
    });

    const pages = getCurrentPages();
    const processingPage = pages[pages.length - 1];

    api.uploadFile(filePath, filename, (progress) => {
      if (processingPage && processingPage.updateProgress) {
        processingPage.updateProgress(progress, '正在上传视频...');
      }
    }).then((res) => {
      if (processingPage && processingPage.startPolling) {
        processingPage.startPolling(res.taskId);
      }
    }).catch((err) => {
      if (processingPage && processingPage.onError) {
        processingPage.onError(err.message || '上传失败');
      }
    });
  },
});
