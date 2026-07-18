// utils/api.js - 后端接口封装（使用 wx.cloud.callContainer）
//
// 通过微信内网专线调用云托管服务，不需要公网域名、不需要备案。
// 前提：app.js 里已经 wx.cloud.init({ env: cloudEnv })

const app = getApp();

const CLOUD_ENV = app.globalData.cloudEnv;
const SERVICE = app.globalData.containerService;
const CHUNK_SIZE = app.globalData.chunkSize;

/**
 * 统一封装 wx.cloud.callContainer
 * @param {object} opts - { path, method, data, header, dataType, responseType }
 * @returns {Promise<object>} resolve(res) / reject(Error)
 */
function callContainer(opts) {
  return new Promise((resolve, reject) => {
    // 合并 header
    var header = Object.assign(
      { 'X-WX-SERVICE': SERVICE },
      opts.header || { 'content-type': 'application/json' }
    );

    // 判断是否 JSON content-type
    var isJson = false;
    for (var key in header) {
      if (key.toLowerCase() === 'content-type' && header[key].indexOf('application/json') !== -1) {
        isJson = true;
        break;
      }
    }

    // 序列化 data：callContainer 不会自动 stringify JSON 对象
    var sendData = opts.data;
    if (isJson && sendData && typeof sendData === 'object') {
      sendData = JSON.stringify(sendData);
    }

    wx.cloud.callContainer({
      config: { env: CLOUD_ENV },
      path: opts.path,
      method: opts.method || 'GET',
      header: header,
      data: sendData,
      dataType: opts.dataType || 'json',
      responseType: opts.responseType || 'text',
      success: (res) => {
        if (res.statusCode !== 200) {
          console.error('[callContainer] HTTP', res.statusCode, opts.path, res.data);
          var msg = opts.errorMsg || ('HTTP ' + res.statusCode);
          if (res.data && res.data.detail) {
            msg = typeof res.data.detail === 'string'
              ? res.data.detail
              : JSON.stringify(res.data.detail);
          }
          reject(new Error(msg));
          return;
        }
        resolve(res);
      },
      fail: (err) => {
        console.error('[callContainer] fail:', opts.path, err);
        var errMsg = typeof err.errMsg === 'string' ? err.errMsg : JSON.stringify(err);
        reject(new Error(errMsg || '调用失败，请检查云环境配置'));
      },
    });
  });
}

// ════════════════════════════════════════
//  1. 短视频链接解析
// ════════════════════════════════════════

/**
 * 解析短视频链接
 */
function parseLink(url) {
  return callContainer({
    path: '/api/parse-link',
    method: 'POST',
    header: { 'content-type': 'application/json' },
    data: { url: url },
    errorMsg: '解析失败',
  }).then((res) => res.data);
}

// ════════════════════════════════════════
//  2. 分片上传大文件
// ════════════════════════════════════════

/**
 * 分片上传大文件
 * @param {string} filePath - 本地文件路径
 * @param {string} filename - 文件名
 * @param {function} onProgress - 进度回调 (0~100)
 * @returns {Promise<{taskId: string}>}
 */
function uploadFile(filePath, filename, onProgress) {
  return new Promise((resolve, reject) => {
    var fs = wx.getFileSystemManager();

    // Step 1: 获取文件信息
    fs.stat({
      path: filePath,
      success: function (statRes) {
        var fileSize = statRes.stats.size;
        var totalChunks = Math.ceil(fileSize / CHUNK_SIZE);

        if (totalChunks === 0) {
          reject(new Error('文件为空'));
          return;
        }

        console.log('[upload] 文件大小: ' + (fileSize / 1024 / 1024).toFixed(1) + 'MB, 分片数: ' + totalChunks);

        // Step 2: 初始化上传
        initUpload(filename, totalChunks).then(function (initRes) {
          var taskId = initRes.task_id;
          console.log('[upload] init 成功, taskId:', taskId);

          // Step 3: 逐片上传
          uploadChunks(filePath, taskId, totalChunks, fileSize, onProgress)
            .then(function () {
              console.log('[upload] 所有分片上传完成');
              // Step 4: 合并并提取
              return completeUpload(taskId);
            })
            .then(function (res) {
              console.log('[upload] complete 成功:', res);
              resolve({ taskId: taskId });
            })
            .catch(function (err) {
              console.error('[upload] 失败:', err);
              reject(err);
            });
        }).catch(function (err) {
          console.error('[upload] init 失败:', err);
          reject(err);
        });
      },
      fail: function (err) {
        console.error('[upload] stat 失败:', err);
        reject(new Error('无法读取文件: ' + (err.errMsg || '')));
      },
    });
  });
}

function initUpload(filename, totalChunks) {
  return callContainer({
    path: '/api/upload/init',
    method: 'POST',
    header: { 'content-type': 'application/json' },
    data: { filename: filename, total_chunks: totalChunks },
    errorMsg: '初始化失败',
  }).then(function (res) { return res.data; });
}

function uploadChunks(filePath, taskId, totalChunks, fileSize, onProgress) {
  return new Promise(function (resolve, reject) {
    var fs = wx.getFileSystemManager();
    var uploaded = 0;

    function uploadNext(index) {
      if (index >= totalChunks) {
        resolve();
        return;
      }

      var position = index * CHUNK_SIZE;
      var length = Math.min(CHUNK_SIZE, fileSize - position);

      console.log('[upload] 开始读取分片', index, 'position:', position, 'length:', length);

      fs.readFile({
        filePath: filePath,
        position: position,
        length: length,
        success: function (readRes) {
          var chunkData = readRes.data; // ArrayBuffer
          console.log('[upload] 分片', index, '读取成功, 大小:', chunkData.byteLength);

          function doUpload(retryCount) {
            wx.cloud.callContainer({
              config: { env: CLOUD_ENV },
              path: '/api/upload/chunk?task_id=' + taskId + '&chunk_index=' + index,
              method: 'POST',
              header: {
                'X-WX-SERVICE': SERVICE,
                'content-type': 'application/octet-stream',
              },
              data: chunkData, // ArrayBuffer 直传
              dataType: 'json',
              success: function (res) {
                if (res.statusCode !== 200) {
                  if (retryCount < 1) {
                    console.error('[upload] 分片 ' + index + ' 返回 HTTP ' + res.statusCode + ', 重试...');
                    doUpload(retryCount + 1);
                  } else {
                    reject(new Error('分片 ' + index + ' 上传失败: HTTP ' + res.statusCode));
                  }
                  return;
                }
                uploaded++;
                var pct = Math.floor((uploaded / totalChunks) * 80);
                if (onProgress) onProgress(pct);
                console.log('[upload] 分片', index, '上传成功, 进度:', pct + '%');
                uploadNext(index + 1);
              },
              fail: function (err) {
                if (retryCount < 1) {
                  console.error('[upload] 分片 ' + index + ' 网络失败, 重试...', err);
                  doUpload(retryCount + 1);
                } else {
                  reject(new Error('上传失败，分片 ' + index + ': ' + (err.errMsg || '')));
                }
              },
            });
          }
          doUpload(0);
        },
        fail: function (err) {
          console.error('[upload] 分片', index, '读取失败:', err);
          reject(new Error('读取分片失败: ' + (err.errMsg || '')));
        },
      });
    }

    uploadNext(0);
  });
}

function completeUpload(taskId) {
  return callContainer({
    path: '/api/upload/complete',
    method: 'POST',
    header: { 'content-type': 'application/json' },
    data: { task_id: taskId },
    errorMsg: '合并失败',
  }).then(function (res) { return res.data; });
}

// ════════════════════════════════════════
//  3. 轮询任务状态
// ════════════════════════════════════════

/**
 * 轮询任务状态
 * @param {string} taskId
 * @param {function} onUpdate - 回调，收到任务状态对象
 * @param {number} interval - 轮询间隔 ms
 * @returns {function} stop - 停止轮询
 */
function pollTask(taskId, onUpdate, interval) {
  interval = interval || 1500;
  var timer = null;
  var stopped = false;

  function check() {
    if (stopped) return;

    wx.cloud.callContainer({
      config: { env: CLOUD_ENV },
      path: '/api/task/' + taskId,
      method: 'GET',
      header: { 'X-WX-SERVICE': SERVICE },
      dataType: 'json',
      success: function (res) {
        if (res.statusCode === 200) {
          var data = res.data;
          if (onUpdate) onUpdate(data);

          if (data.status === 'done' || data.status === 'error') {
            stopped = true;
            return;
          }
        }
      },
      complete: function () {
        if (!stopped) {
          timer = setTimeout(check, interval);
        }
      },
    });
  }

  check();

  return function stop() {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}

// ════════════════════════════════════════
//  4. 音频下载（通过 callContainer 获取，保存到本地）
// ════════════════════════════════════════

/**
 * 下载音频文件到本地临时路径
 * callContainer 不支持公网 URL 访问，所以先通过内网拉取文件，保存到本地。
 *
 * @param {string} taskId
 * @param {function} onProgress - 可选，下载进度回调 (0~100)
 * @returns {Promise<string>} localFilePath - 本地文件路径
 */
function downloadAudio(taskId, onProgress) {
  return new Promise(function (resolve, reject) {
    // 先尝试 responseType: 'arraybuffer' 直接获取二进制
    wx.cloud.callContainer({
      config: { env: CLOUD_ENV },
      path: '/api/audio/' + taskId,
      method: 'GET',
      header: { 'X-WX-SERVICE': SERVICE },
      responseType: 'arraybuffer',
      success: function (res) {
        if (res.statusCode !== 200) {
          // 如果直接获取二进制失败，回退到 base64 方式
          console.log('[audio] arraybuffer 模式失败, 尝试 base64...');
          _downloadAudioBase64(taskId, onProgress, resolve, reject);
          return;
        }

        var data = res.data;
        // 如果返回的是 ArrayBuffer，直接保存
        if (data instanceof ArrayBuffer) {
          _saveAudioFile(taskId, data, resolve, reject);
        } else if (typeof data === 'object' && data.audio) {
          // base64 模式回退的响应
          var ab = _base64ToArrayBuffer(data.audio);
          _saveAudioFile(taskId, ab, resolve, reject);
        } else {
          // 未知格式，尝试 base64 回退
          _downloadAudioBase64(taskId, onProgress, resolve, reject);
        }
      },
      fail: function (err) {
        console.error('[audio] callContainer fail:', err);
        // 回退到 base64 方式
        _downloadAudioBase64(taskId, onProgress, resolve, reject);
      },
    });
  });
}

function _downloadAudioBase64(taskId, onProgress, resolve, reject) {
  wx.cloud.callContainer({
    config: { env: CLOUD_ENV },
    path: '/api/audio-base64/' + taskId,
    method: 'GET',
    header: { 'X-WX-SERVICE': SERVICE },
    dataType: 'json',
    success: function (res) {
      if (res.statusCode !== 200 || !res.data || !res.data.audio) {
        reject(new Error('音频下载失败: HTTP ' + res.statusCode));
        return;
      }
      var ab = _base64ToArrayBuffer(res.data.audio);
      _saveAudioFile(taskId, ab, resolve, reject);
    },
    fail: function (err) {
      reject(new Error('下载失败: ' + (err.errMsg || '')));
    },
  });
}

function _base64ToArrayBuffer(base64) {
  // 微信小程序 base64 → ArrayBuffer
  return wx.base64ToArrayBuffer(base64);
}

function _saveAudioFile(taskId, arrayBuffer, resolve, reject) {
  var fs = wx.getFileSystemManager();
  var filePath = wx.env.USER_DATA_PATH + '/audio_' + taskId + '.mp3';

  fs.writeFile({
    filePath: filePath,
    data: arrayBuffer,
    success: function () {
      console.log('[audio] 保存成功:', filePath, '大小:', arrayBuffer.byteLength);
      resolve(filePath);
    },
    fail: function (err) {
      console.error('[audio] 保存失败:', err);
      reject(new Error('保存音频失败: ' + (err.errMsg || '')));
    },
  });
}

// ════════════════════════════════════════
//  5. 删除任务
// ════════════════════════════════════════

function deleteTask(taskId) {
  return callContainer({
    path: '/api/task/' + taskId,
    method: 'DELETE',
    errorMsg: '删除失败',
  }).then(function (res) { return res.data; });
}

// ════════════════════════════════════════
//  导出
// ════════════════════════════════════════

module.exports = {
  parseLink: parseLink,
  uploadFile: uploadFile,
  pollTask: pollTask,
  downloadAudio: downloadAudio,
  deleteTask: deleteTask,
  callContainer: callContainer,
};
