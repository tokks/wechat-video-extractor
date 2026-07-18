// pages/result/result.js - 结果页
const api = require('../../utils/api.js');

Page({
  data: {
    taskId: '',
    audioLocalPath: '',  // 本地音频文件路径
    audioSize: 0,
    audioSizeText: '',
    isPlaying: false,
    isForwarding: false,
    forwardProgress: 0,
    filename: '',
    isLoading: true,       // 正在下载音频
    loadError: '',
  },

  audioCtx: null,

  onLoad(options) {
    const taskId = options.taskId || '';
    const audioSize = parseInt(options.audioSize) || 0;
    const defaultName = 'audio_' + taskId + '.mp3';

    this.setData({
      taskId,
      audioSize,
      audioSizeText: this._formatSize(audioSize),
      filename: defaultName,
      isLoading: true,
    });

    // 通过 callContainer 下载音频到本地
    this._downloadAudio(taskId);
  },

  // 下载音频文件（通过微信内网 callContainer）
  _downloadAudio(taskId) {
    api.downloadAudio(taskId).then((localPath) => {
      this.setData({
        audioLocalPath: localPath,
        isLoading: false,
      });

      // 创建音频上下文，使用本地文件路径
      this.audioCtx = wx.createInnerAudioContext();
      this.audioCtx.src = localPath;

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

      console.log('[result] 音频下载完成:', localPath);
    }).catch((err) => {
      console.error('[result] 音频下载失败:', err);
      this.setData({
        isLoading: false,
        loadError: err.message || '音频下载失败',
      });
      wx.showToast({ title: '音频下载失败: ' + (err.message || ''), icon: 'none' });
    });
  },

  onUnload() {
    if (this.audioCtx) {
      this.audioCtx.destroy();
    }
  },

  // 播放/暂停
  onTogglePlay() {
    if (!this.audioCtx) {
      wx.showToast({ title: '音频尚未就绪', icon: 'none' });
      return;
    }
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
    if (!this.data.audioLocalPath) {
      wx.showToast({ title: '音频尚未下载完成', icon: 'none' });
      return;
    }

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

    // 音频已经在本地了，直接复制到目标路径
    fs.copyFile({
      srcPath: this.data.audioLocalPath,
      destPath: localPath,
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
            // 用户取消分享不算失败
            if (err.errMsg && err.errMsg.indexOf('cancel') < 0) {
              wx.showToast({ title: '转发失败', icon: 'none' });
            }
          },
        });
      },
      fail: (err) => {
        this.setData({ isForwarding: false });
        wx.showToast({ title: '保存失败: ' + (err.errMsg || ''), icon: 'none' });
      },
    });

    // 模拟进度（文件已在本地，复制很快）
    this.setData({ forwardProgress: 50 });
    setTimeout(() => {
      if (this.data.isForwarding) {
        this.setData({ forwardProgress: 100 });
      }
    }, 500);
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
