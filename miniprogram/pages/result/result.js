// pages/result/result.js - 结果页
const api = require('../../utils/api.js');

Page({
  data: {
    taskId: '',
    audioUrl: '',
    audioSize: 0,
    audioSizeText: '',
    isPlaying: false,
    isDownloading: false,
    downloadProgress: 0,
    savedPath: '',
  },

  audioCtx: null,

  onLoad(options) {
    const taskId = options.taskId || '';
    const audioSize = parseInt(options.audioSize) || 0;

    this.setData({
      taskId,
      audioUrl: api.getAudioUrl(taskId),
      audioSize,
      audioSizeText: this._formatSize(audioSize),
    });

    // 创建音频上下文
    this.audioCtx = wx.createInnerAudioContext();
    this.audioCtx.src = this.data.audioUrl;

    this.audioCtx.onPlay(() => {
      this.setData({ isPlaying: true });
    });

    this.audioCtx.onPause(() => {
      this.setData({ isPlaying: false });
    });

    this.audioCtx.onStop(() => {
      this.setData({ isPlaying: false });
    });

    this.audioCtx.onEnded(() => {
      this.setData({ isPlaying: false });
    });

    this.audioCtx.onError((err) => {
      console.error('[audio error]', err);
      wx.showToast({ title: '音频加载失败', icon: 'none' });
    });
  },

  onUnload() {
    if (this.audioCtx) {
      this.audioCtx.destroy();
    }
  },

  // 播放/暂停
  onTogglePlay() {
    if (this.data.isPlaying) {
      this.audioCtx.pause();
    } else {
      this.audioCtx.play();
    }
  },

  // 下载到本地
  onDownload() {
    if (this.data.isDownloading) return;

    this.setData({ isDownloading: true, downloadProgress: 0 });

    const downloadTask = wx.downloadFile({
      url: this.data.audioUrl,
      success: (res) => {
        if (res.statusCode === 200) {
          // 保存到手机
          wx.saveFile({
            tempFilePath: res.tempFilePath,
            success: (saveRes) => {
              this.setData({
                savedPath: saveRes.savedFilePath,
                isDownloading: false,
              });
              wx.showToast({ title: '已保存到本地', icon: 'success' });
            },
            fail: () => {
              this.setData({ isDownloading: false });
              wx.showToast({ title: '保存失败', icon: 'none' });
            },
          });
        } else {
          this.setData({ isDownloading: false });
          wx.showToast({ title: '下载失败', icon: 'none' });
        }
      },
      fail: () => {
        this.setData({ isDownloading: false });
        wx.showToast({ title: '下载失败', icon: 'none' });
      },
    });

    if (downloadTask) {
      downloadTask.onProgressUpdate((res) => {
        this.setData({ downloadProgress: res.progress });
      });
    }
  },

  // 用其他应用打开
  onOpenWithOther() {
    if (!this.data.savedPath) {
      wx.showToast({ title: '请先下载', icon: 'none' });
      return;
    }

    wx.openDocument({
      filePath: this.data.savedPath,
      success: () => {
        // openDocument 可能不支持 mp3，回退到分享
      },
      fail: () => {
        // 回退：通过分享转发
        wx.showShareMenu({
          withShareTicket: true,
          menus: ['shareAppMessage'],
        });
        wx.showToast({ title: '请点击右上角转发', icon: 'none' });
      },
    });
  },

  // 再提取一个
  onProcessAnother() {
    wx.redirectTo({ url: '/pages/index/index' });
  },

  // 格式化文件大小
  _formatSize(bytes) {
    if (!bytes) return '未知';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  },
});
