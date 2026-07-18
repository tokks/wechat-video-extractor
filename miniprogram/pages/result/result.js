// pages/result/result.js - 结果页
const api = require('../../utils/api.js');

Page({
  data: {
    taskId: '',
    audioUrl: '',
    audioSize: 0,
    audioSizeText: '',
    isPlaying: false,
    isForwarding: false,
    forwardProgress: 0,
    filename: '',
  },

  audioCtx: null,

  onLoad(options) {
    const taskId = options.taskId || '';
    const audioSize = parseInt(options.audioSize) || 0;
    const defaultName = 'audio_' + taskId + '.mp3';

    this.setData({
      taskId,
      audioUrl: api.getAudioUrl(taskId),
      audioSize,
      audioSizeText: this._formatSize(audioSize),
      filename: defaultName,
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

  // 修改文件名
  onFilenameInput(e) {
    this.setData({ filename: e.detail.value });
  },

  // 转发到微信聊天
  onForwardToChat() {
    if (this.data.isForwarding) return;

    let filename = this.data.filename.trim();
    if (!filename) {
      wx.showToast({ title: '请输入文件名', icon: 'none' });
      return;
    }
    // 确保后缀是 .mp3
    if (!filename.toLowerCase().endsWith('.mp3')) {
      filename = filename + '.mp3';
      this.setData({ filename });
    }

    this.setData({ isForwarding: true, forwardProgress: 0 });

    const fs = wx.getFileSystemManager();
    const localPath = wx.env.USER_DATA_PATH + '/' + filename;

    // 下载音频文件
    const downloadTask = wx.downloadFile({
      url: this.data.audioUrl,
      success: (res) => {
        if (res.statusCode !== 200) {
          this.setData({ isForwarding: false });
          wx.showToast({ title: '音频下载失败', icon: 'none' });
          return;
        }

        // 保存到本地文件系统
        fs.saveFile({
          tempFilePath: res.tempFilePath,
          filePath: localPath,
          success: () => {
            // 唤起转发文件到聊天
            wx.shareFileMessage({
              filePath: localPath,
              fileName: filename,
              success: () => {
                this.setData({ isForwarding: false });
                wx.showToast({ title: '已选择聊天', icon: 'success' });
              },
              fail: (err) => {
                this.setData({ isForwarding: false });
                wx.showToast({ title: '转发取消或失败', icon: 'none' });
              },
            });
          },
          fail: (err) => {
            this.setData({ isForwarding: false });
            wx.showToast({ title: '保存失败: ' + (err.errMsg || ''), icon: 'none' });
          },
        });
      },
      fail: (err) => {
        this.setData({ isForwarding: false });
        wx.showToast({ title: '下载失败: ' + (err.errMsg || ''), icon: 'none' });
      },
    });

    if (downloadTask) {
      downloadTask.onProgressUpdate((res) => {
        this.setData({ forwardProgress: res.progress });
      });
    }
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
