// pages/processing/processing.js - 处理中页面
const api = require('../../utils/api.js');

Page({
  data: {
    taskId: '',
    type: '',         // "link" or "upload"
    platform: '',
    filename: '',
    status: 'pending',
    progress: 0,
    message: '正在初始化...',
    statusText: '处理中',
    isError: false,
  },

  stopPolling: null,
  _uploadTimer: null,

  onLoad(options) {
    this.setData({
      type: options.type || 'link',
      platform: options.platform || '',
      filename: options.filename ? decodeURIComponent(options.filename) : '',
      taskId: options.taskId || '',
    });

    // 如果是链接解析，已有 taskId，直接轮询
    if (this.data.taskId) {
      this.startPolling(this.data.taskId);
    }

    // 上传类型：轮询 globalData.uploadState 获取上传进度
    if (this.data.type === 'upload') {
      this.setData({
        progress: 0,
        message: '准备上传...',
      });
      this._pollUploadState();
    }
  },

  onUnload() {
    if (this._uploadTimer) {
      clearTimeout(this._uploadTimer);
      this._uploadTimer = null;
    }
    if (this.stopPolling) {
      this.stopPolling();
    }
  },

  // 轮询 globalData 中的上传状态
  _pollUploadState() {
    var app = getApp();
    var state = app.globalData.uploadState;

    if (!state) {
      this.onError('上传状态异常');
      return;
    }

    if (state.error) {
      this.onError(state.error);
      return;
    }

    if (state.taskId) {
      // 上传完成，开始轮询任务状态
      this.startPolling(state.taskId);
      return;
    }

    // 更新进度
    this.updateProgress(state.progress, state.message);

    // 300ms 后继续轮询
    var self = this;
    this._uploadTimer = setTimeout(function () {
      self._pollUploadState();
    }, 300);
  },

  // 供首页调用的方法
  updateProgress(progress, message) {
    this.setData({ progress, message: message || this.data.message });
  },

  startPolling(taskId) {
    this.setData({ taskId });

    this.stopPolling = api.pollTask(taskId, (data) => {
      let statusText = '处理中';
      switch (data.status) {
        case 'downloading': statusText = '下载中'; break;
        case 'merging': statusText = '合并中'; break;
        case 'extracting': statusText = '提取中'; break;
        case 'done': statusText = '完成'; break;
        case 'error': statusText = '失败'; break;
      }

      this.setData({
        status: data.status,
        progress: Math.max(this.data.progress, data.progress),
        message: data.message,
        statusText,
        isError: data.status === 'error',
      });

      if (data.status === 'done') {
        setTimeout(() => {
          wx.redirectTo({
            url: '/pages/result/result?taskId=' + taskId +
                 '&audioSize=' + (data.audio_size || 0),
          });
        }, 500);
      }
    });
  },

  onError(message) {
    this.setData({
      isError: true,
      message: message,
      statusText: '失败',
      status: 'error',
    });
  },

  onRetry() {
    wx.navigateBack();
  },
});
