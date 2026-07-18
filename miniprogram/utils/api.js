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

        console.log('[upload] 文件大小: ' + (fileSize / 1024 / 1024).toFixed(1) + 'MB, 分片数: ' + totalChunks);

        // Step 2: 初始化上传
        initUpload(filename, totalChunks).then((initRes) => {
          const taskId = initRes.task_id;
          console.log('[upload] init 成功, taskId:', taskId);

          // Step 3: 逐片上传
          uploadChunks(filePath, taskId, totalChunks, fileSize, onProgress)
            .then(() => {
              console.log('[upload] 所有分片上传完成');
              // Step 4: 合并并提取
              completeUpload(taskId).then((res) => {
                console.log('[upload] complete 成功:', res);
                resolve({ taskId });
              }).catch((err) => {
                console.error('[upload] complete 失败:', err);
                reject(err);
              });
            })
            .catch((err) => {
              console.error('[upload] 分片上传失败:', err);
              reject(err);
            });
        }).catch((err) => {
          console.error('[upload] init 失败:', err);
          reject(err);
        });
      },
      fail: (err) => {
        console.error('[upload] stat 失败:', err);
        reject(new Error('无法读取文件: ' + (err.errMsg || '')));
      },
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
    const fs = wx.getFileSystemManager();
    let uploaded = 0;
    const tempDir = wx.env.USER_DATA_PATH;

    function uploadNext(index) {
      if (index >= totalChunks) {
        resolve();
        return;
      }

      // 用 position+length 读取真正的分片数据（不是整个文件）
      const position = index * CHUNK_SIZE;
      const length = Math.min(CHUNK_SIZE, fileSize - position);
      const tempPath = tempDir + '/chunk_' + index + '.tmp';

      console.log('[upload] 开始读取分片', index, 'position:', position, 'length:', length);

      fs.readFile({
        filePath: filePath,
        position: position,
        length: length,
        success: (readRes) => {
          console.log('[upload] 分片', index, '读取成功, 大小:', readRes.data ? readRes.data.byteLength : 0);
          // 写成临时文件供 wx.uploadFile 使用
          fs.writeFile({
            filePath: tempPath,
            data: readRes.data,
            success: () => {
              console.log('[upload] 分片', index, '临时文件写入成功');
              function doUpload(retryCount) {
                wx.uploadFile({
                  url: BASE + '/api/upload/chunk',
                  filePath: tempPath,
                  name: 'chunk',
                  formData: { task_id: taskId, chunk_index: index },
                  success: (res) => {
                    // 必须检查状态码，4xx/5xx 不是成功
                    if (res.statusCode !== 200) {
                      if (retryCount < 1) {
                        console.error('[upload] 分片 ' + index + ' 返回 HTTP ' + res.statusCode + ', 重试...');
                        doUpload(retryCount + 1);
                      } else {
                        fs.unlink({ filePath: tempPath, fail: function() {} });
                        reject(new Error('分片 ' + index + ' 上传失败: HTTP ' + res.statusCode));
                      }
                      return;
                    }
                    // 清理临时文件
                    fs.unlink({ filePath: tempPath, fail: function() {} });
                    uploaded++;
                    const pct = Math.floor((uploaded / totalChunks) * 80);
                    if (onProgress) onProgress(pct);
                    console.log('[upload] 分片', index, '上传成功, 进度:', pct + '%');
                    uploadNext(index + 1);
                  },
                  fail: (err) => {
                    if (retryCount < 1) {
                      console.error('[upload] 分片 ' + index + ' 网络失败, 重试...', err);
                      doUpload(retryCount + 1);
                    } else {
                      fs.unlink({ filePath: tempPath, fail: function() {} });
                      reject(new Error('上传失败，分片 ' + index + ': ' + (err.errMsg || '')));
                    }
                  },
                });
              }
              doUpload(0);
            },
            fail: (err) => {
              console.error('[upload] 分片', index, '临时文件写入失败:', err);
              reject(new Error('写入临时文件失败: ' + (err.errMsg || '')));
            },
          });
        },
        fail: (err) => {
          console.error('[upload] 分片', index, '读取失败:', err);
          reject(new Error('读取分片失败: ' + (err.errMsg || '')));
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
