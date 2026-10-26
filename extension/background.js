(function (root) {
  'use strict';

  function resolveCore() {
    if (root && root.PocketNotesCore) {
      return root.PocketNotesCore;
    }
    if (typeof self !== 'undefined' && self.PocketNotesCore) {
      return self.PocketNotesCore;
    }
    if (typeof window !== 'undefined' && window.PocketNotesCore) {
      return window.PocketNotesCore;
    }
    if (typeof importScripts === 'function') {
      importScripts('core.js');
      if (root && root.PocketNotesCore) return root.PocketNotesCore;
      if (typeof self !== 'undefined' && self.PocketNotesCore) return self.PocketNotesCore;
    }
    if (typeof require === 'function') {
      return require('./core.js');
    }
    throw new Error('Không thể khởi tạo PocketNotesCore trong dịch vụ nền.');
  }

  const core = resolveCore();
  const STORAGE_KEY = core.STORAGE_KEY || 'pocket_notes_data';
  const ACTIONS = core.ACTIONS || {
    LIST_NOTES: 'notes:list',
    CREATE_NOTE: 'notes:create',
    UPDATE_NOTE: 'notes:update',
    DELETE_NOTE: 'notes:delete'
  };

  const SUPPORTED_ACTIONS = new Set([
    ACTIONS.LIST_NOTES,
    ACTIONS.CREATE_NOTE,
    ACTIONS.UPDATE_NOTE,
    ACTIONS.DELETE_NOTE
  ]);

  let operationQueue = Promise.resolve();

  function enqueueOperation(taskFn) {
    const nextTask = operationQueue.then(taskFn, taskFn);
    operationQueue = nextTask.catch(function () {});
    return nextTask;
  }

  function safeSendResponse(sendResponse, payload) {
    if (typeof sendResponse !== 'function') return;
    try {
      sendResponse(payload);
    } catch (_) {
      // Popup có thể đã đóng khi tác vụ nền hoàn tất; việc lưu trữ đã thực hiện xong.
    }
  }

  function isTrustedPopupSender(sender, runtime) {
    if (!sender || typeof sender !== 'object') return false;
    if (sender.tab) return false;

    const runtimeId = runtime && typeof runtime.id === 'string' && runtime.id ? runtime.id : null;
    if (runtimeId) {
      if (sender.id !== runtimeId) return false;
    } else {
      const hasId = typeof sender.id === 'string' && sender.id.trim().length > 0;
      const hasUrl = typeof sender.url === 'string' && sender.url.trim().length > 0;
      if (!hasId && !hasUrl) return false;
    }

    if (sender.origin !== undefined) {
      if (typeof sender.origin !== 'string') return false;
      if (runtimeId) {
        if (sender.origin !== 'chrome-extension://' + runtimeId) return false;
      } else if (!/^chrome-extension:\/\/[^/]+$/.test(sender.origin)) {
        return false;
      }
    }

    if (sender.url !== undefined) {
      if (typeof sender.url !== 'string') return false;
      if (runtime && typeof runtime.getURL === 'function') {
        const expectedPopupUrl = runtime.getURL('popup.html');
        if (typeof expectedPopupUrl === 'string' && expectedPopupUrl) {
          const cleanUrl = sender.url.split('#')[0].split('?')[0];
          if (cleanUrl !== expectedPopupUrl) return false;
          return true;
        }
      }
      try {
        const parsed = new URL(sender.url);
        if (parsed.protocol !== 'chrome-extension:') return false;
        if (runtimeId && parsed.host !== runtimeId) return false;
        if (parsed.pathname !== '/popup.html') return false;
      } catch (e) {
        return false;
      }
    }

    return true;
  }

  function readStorageRaw() {
    return new Promise(function (resolve, reject) {
      if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local ||
          typeof chrome.storage.local.get !== 'function') {
        reject({
          code: 'STORAGE_UNAVAILABLE',
          message: 'Không thể truy cập chrome.storage.local.'
        });
        return;
      }

      try {
        chrome.storage.local.get([STORAGE_KEY], function (result) {
          if (chrome.runtime && chrome.runtime.lastError) {
            reject({
              code: 'STORAGE_READ_ERROR',
              message: 'Lỗi khi đọc bộ nhớ: ' + chrome.runtime.lastError.message
            });
            return;
          }
          resolve(result && typeof result === 'object' ? result : {});
        });
      } catch (err) {
        reject({
          code: 'STORAGE_READ_ERROR',
          message: 'Lỗi khi đọc bộ nhớ: ' + (err && err.message ? err.message : String(err))
        });
      }
    });
  }

  function writeStorageRaw(nextNotes) {
    return new Promise(function (resolve, reject) {
      if (typeof chrome === 'undefined' || !chrome.storage || !chrome.storage.local ||
          typeof chrome.storage.local.set !== 'function') {
        reject({
          code: 'STORAGE_UNAVAILABLE',
          message: 'Không thể lưu: thiếu quyền storage.'
        });
        return;
      }

      const payload = {};
      payload[STORAGE_KEY] = nextNotes;

      try {
        chrome.storage.local.set(payload, function () {
          if (chrome.runtime && chrome.runtime.lastError) {
            reject({
              code: 'STORAGE_WRITE_ERROR',
              message: 'Lỗi lưu trữ: ' + chrome.runtime.lastError.message
            });
            return;
          }
          resolve();
        });
      } catch (err) {
        reject({
          code: 'STORAGE_WRITE_ERROR',
          message: 'Lỗi lưu trữ: ' + (err && err.message ? err.message : String(err))
        });
      }
    });
  }

  async function readAndValidateCurrentNotes() {
    const rawResult = await readStorageRaw();
    const validation = core.validateStoredNotes(rawResult[STORAGE_KEY]);
    if (!validation.valid) {
      const err = new Error(validation.error);
      err.code = 'CORRUPT_STORAGE';
      throw err;
    }
    return validation.notes;
  }

  async function handleListNotes() {
    const currentNotes = await readAndValidateCurrentNotes();
    return {
      ok: true,
      notes: currentNotes
    };
  }

  async function handleCreateNote(message) {
    const currentNotes = await readAndValidateCurrentNotes();

    const inputValidation = core.validateNoteInput(message ? message.input : null);
    if (!inputValidation.valid) {
      return {
        ok: false,
        code: 'INVALID_INPUT',
        error: inputValidation.error
      };
    }

    if (currentNotes.length >= core.MAX_NOTES) {
      return {
        ok: false,
        code: 'MAX_NOTES_EXCEEDED',
        error: 'Đã vượt quá giới hạn tối đa ' + core.MAX_NOTES + ' ghi chú.'
      };
    }

    const existingIds = new Set(currentNotes.map(function (n) { return n.id; }));
    let newNote = core.createNote(message.input);
    while (existingIds.has(newNote.id)) {
      newNote = core.createNote(message.input);
    }

    const nextNotes = [newNote, ...currentNotes];
    const nextValidation = core.validateStoredNotes(nextNotes);
    if (!nextValidation.valid) {
      return {
        ok: false,
        code: 'VALIDATION_FAILED',
        error: nextValidation.error
      };
    }

    await writeStorageRaw(nextValidation.notes);
    return {
      ok: true,
      note: core.cloneNote(newNote),
      notes: nextValidation.notes
    };
  }

  async function handleUpdateNote(message) {
    const currentNotes = await readAndValidateCurrentNotes();

    const targetId = message && typeof message.id === 'string' ? message.id.trim() : '';
    const snapshot = message ? message.snapshot : null;

    if (!targetId || !core.validateStoredNote(snapshot) || snapshot.id !== targetId) {
      return {
        ok: false,
        code: 'INVALID_SNAPSHOT',
        error: 'Bản chụp ghi chú gốc không hợp lệ để cập nhật.'
      };
    }

    const inputValidation = core.validateNoteInput(message ? message.input : null);
    if (!inputValidation.valid) {
      return {
        ok: false,
        code: 'INVALID_INPUT',
        error: inputValidation.error
      };
    }

    const index = currentNotes.findIndex(function (n) { return n.id === targetId; });
    if (index === -1) {
      return {
        ok: false,
        code: 'CONFLICT_DELETED',
        conflict: true,
        error: 'Xung đột dữ liệu: Ghi chú này đã bị xóa ở cửa sổ khác. Bản nháp vẫn được giữ lại.'
      };
    }

    const currentRecord = currentNotes[index];
    if (!core.isSameNoteSnapshot(currentRecord, snapshot)) {
      return {
        ok: false,
        code: 'CONFLICT_MODIFIED',
        conflict: true,
        error: 'Xung đột dữ liệu: Ghi chú này đã bị thay đổi ở cửa sổ khác. Bản nháp vẫn được giữ lại.'
      };
    }

    const updatedNote = core.updateNote(currentRecord, message.input);
    const nextNotes = currentNotes.slice();
    nextNotes[index] = updatedNote;

    const nextValidation = core.validateStoredNotes(nextNotes);
    if (!nextValidation.valid) {
      return {
        ok: false,
        code: 'VALIDATION_FAILED',
        error: nextValidation.error
      };
    }

    await writeStorageRaw(nextValidation.notes);
    return {
      ok: true,
      note: core.cloneNote(updatedNote),
      notes: nextValidation.notes
    };
  }

  async function handleDeleteNote(message) {
    const currentNotes = await readAndValidateCurrentNotes();

    const targetId = message && typeof message.id === 'string' ? message.id.trim() : '';
    const snapshot = message ? message.snapshot : null;

    if (!targetId || !core.validateStoredNote(snapshot) || snapshot.id !== targetId) {
      return {
        ok: false,
        code: 'INVALID_SNAPSHOT',
        error: 'Bản chụp ghi chú gốc không hợp lệ để xóa.'
      };
    }

    const index = currentNotes.findIndex(function (n) { return n.id === targetId; });
    if (index === -1) {
      return {
        ok: false,
        code: 'CONFLICT_DELETED',
        conflict: true,
        error: 'Xung đột dữ liệu: Ghi chú không tồn tại hoặc đã bị xóa ở cửa sổ khác.'
      };
    }

    const currentRecord = currentNotes[index];
    if (!core.isSameNoteSnapshot(currentRecord, snapshot)) {
      return {
        ok: false,
        code: 'CONFLICT_MODIFIED',
        conflict: true,
        error: 'Xung đột dữ liệu: Ghi chú đã bị chỉnh sửa ở cửa sổ khác nên không thể xóa.'
      };
    }

    const nextNotes = currentNotes.filter(function (n) { return n.id !== targetId; });
    const nextValidation = core.validateStoredNotes(nextNotes);
    if (!nextValidation.valid) {
      return {
        ok: false,
        code: 'VALIDATION_FAILED',
        error: nextValidation.error
      };
    }

    await writeStorageRaw(nextValidation.notes);
    return {
      ok: true,
      notes: nextValidation.notes
    };
  }

  async function dispatchOperation(action, message) {
    try {
      switch (action) {
        case ACTIONS.LIST_NOTES:
          return await handleListNotes();
        case ACTIONS.CREATE_NOTE:
          return await handleCreateNote(message);
        case ACTIONS.UPDATE_NOTE:
          return await handleUpdateNote(message);
        case ACTIONS.DELETE_NOTE:
          return await handleDeleteNote(message);
        default:
          return {
            ok: false,
            code: 'UNKNOWN_ACTION',
            error: 'Yêu cầu không hợp lệ.'
          };
      }
    } catch (err) {
      return {
        ok: false,
        code: (err && err.code) ? err.code : 'STORAGE_ERROR',
        error: (err && err.message) ? err.message : 'Lỗi xử lý lưu trữ trong dịch vụ nền.'
      };
    }
  }

  function getMessageAction(message) {
    if (!message || typeof message !== 'object' || Array.isArray(message)) return '';
    if (typeof message.type === 'string') return message.type;
    if (typeof message.action === 'string') return message.action;
    return '';
  }

  function onRuntimeMessage(message, sender, sendResponse) {
    const runtime = typeof chrome !== 'undefined' && chrome.runtime ? chrome.runtime : null;
    if (!isTrustedPopupSender(sender, runtime)) {
      safeSendResponse(sendResponse, {
        ok: false,
        code: 'FORBIDDEN_SENDER',
        error: 'Nguồn gửi thông điệp không được phép.'
      });
      return false;
    }

    const action = getMessageAction(message);
    if (!SUPPORTED_ACTIONS.has(action)) {
      return false;
    }

    enqueueOperation(function () {
      return dispatchOperation(action, message);
    }).then(function (response) {
      safeSendResponse(sendResponse, response);
    }).catch(function (err) {
      safeSendResponse(sendResponse, {
        ok: false,
        code: 'INTERNAL_ERROR',
        error: (err && err.message) ? err.message : 'Lỗi dịch vụ nền không xác định.'
      });
    });

    return true;
  }

  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage &&
      typeof chrome.runtime.onMessage.addListener === 'function') {
    chrome.runtime.onMessage.addListener(onRuntimeMessage);
  }

  const exported = {
    ACTIONS: ACTIONS,
    STORAGE_KEY: STORAGE_KEY,
    isTrustedPopupSender: isTrustedPopupSender,
    enqueueOperation: enqueueOperation,
    onRuntimeMessage: onRuntimeMessage
  };

  if (typeof module === 'object' && module.exports) {
    module.exports = exported;
  } else if (root) {
    root.PocketNotesBackground = exported;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
