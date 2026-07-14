// utils/api.js - 后端接口封装

const app = getApp();

const BASE = app.globalData.baseUrl;
const CHUNK_SIZE = app.globalData.chunkSize;

/**
 * 解析短视频链接
 */
function parseLink(url) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE + '/api/parse-link',
      method: 'POST',
      header: { 'Content-Type': 'application/x-www-form-urlencoded' },
      data: { url: url },
      success: (res) => {
        if (res.statusCode === 200) {
          resolve(res.data);
        } else {
          reject(new Error(res.data.detail || '解析失败'));
        }
      },
      fail: (err) => reject(err),
    });
  });
}

/**
 * 分片上传大文件
 * @param {string} filePath - 本地文件路径
 * @param {string} filename - 文件名
 * @param {function} onProgress - 进度回调 (0~100)
 */
function uploadFile(filePath, filename, onProgress) {
  return new Promise((resolve, reject) => {
    const fs = wx.getFileSystemManager();

    // Step 1: 获取文件信息
    fs.stat({
      path: filePath,
      success: (statRes) => {
        const fileSize = statRes.stats.size;
        const totalChunks = Math.ceil(fileSize / CHUNK_SIZE);

        if (totalChunks === 0) {
          reject(new Error('文件为空'));
          return;
        }

        console.log(`[upload] 文件大小: ${(fileSize/1024/1024).toFixed(1)}MB, 分片数: ${totalChunks}`);

        // Step 2: 初始化上传
        initUpload(filename, totalChunks).then((initRes) => {
          const taskId = initRes.task_id;

          // Step 3: 逐片上传
          uploadChunks(filePath, taskId, totalChunks, fileSize, onProgress)
            .then(() => {
              // Step 4: 合并并提取
              completeUpload(taskId).then((res) => {
                resolve({ taskId });
              }).catch(reject);
            })
            .catch(reject);
        }).catch(reject);
      },
      fail: (err) => reject(new Error('无法读取文件: ' + err.errMsg)),
    });
  });
}

function initUpload(filename, totalChunks) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE + '/api/upload/init',
      method: 'POST',
      header: { 'Content-Type': 'application/x-www-form-urlencoded' },
      data: { filename, total_chunks: totalChunks },
      success: (res) => res.statusCode === 200 ? resolve(res.data) : reject(new Error('初始化失败')),
      fail: reject,
    });
  });
}

function uploadChunks(filePath, taskId, totalChunks, fileSize, onProgress) {
  return new Promise((resolve, reject) => {
    let uploaded = 0;

    function uploadNext(index) {
      if (index >= totalChunks) {
        resolve();
        return;
      }

      const start = index * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, fileSize);

      wx.uploadFile({
        url: BASE + '/api/upload/chunk',
        filePath: filePath,
        name: 'chunk',
        formData: {
          task_id: taskId,
          chunk_index: index,
        },
        success: (res) => {
          uploaded++;
          const pct = Math.floor((uploaded / totalChunks) * 80);
          if (onProgress) onProgress(pct);
          uploadNext(index + 1);
        },
        fail: (err) => {
          console.error('[upload] 分片失败:', index, err);
          // 重试一次
          wx.uploadFile({
            url: BASE + '/api/upload/chunk',
            filePath: filePath,
            name: 'chunk',
            formData: { task_id: taskId, chunk_index: index },
            success: () => {
              uploaded++;
              const pct = Math.floor((uploaded / totalChunks) * 80);
              if (onProgress) onProgress(pct);
              uploadNext(index + 1);
            },
            fail: (err2) => reject(new Error('上传失败，分片 ' + index)),
          });
        },
      });
    }

    uploadNext(0);
  });
}

function completeUpload(taskId) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE + '/api/upload/complete',
      method: 'POST',
      header: { 'Content-Type': 'application/x-www-form-urlencoded' },
      data: { task_id: taskId },
      success: (res) => res.statusCode === 200 ? resolve(res.data) : reject(new Error('合并失败')),
      fail: reject,
    });
  });
}

/**
 * 轮询任务状态
 */
function pollTask(taskId, onUpdate, interval) {
  interval = interval || 1500;
  let timer = null;
  let stopped = false;

  function check() {
    if (stopped) return;

    wx.request({
      url: BASE + '/api/task/' + taskId,
      method: 'GET',
      success: (res) => {
        if (res.statusCode === 200) {
          const data = res.data;
          if (onUpdate) onUpdate(data);

          if (data.status === 'done' || data.status === 'error') {
            stopped = true;
            return;
          }
        }
      },
      complete: () => {
        if (!stopped) {
          timer = setTimeout(check, interval);
        }
      },
    });
  }

  check();

  // 返回停止函数
  return function stop() {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}

/**
 * 获取音频下载 URL
 */
function getAudioUrl(taskId) {
  return BASE + '/api/audio/' + taskId;
}

module.exports = {
  parseLink,
  uploadFile,
  pollTask,
  getAudioUrl,
  BASE,
};
