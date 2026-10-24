(function () {
  'use strict';

  const core = window.PocketNotesCore;
  const STORAGE_KEY = 'pocket_notes_data';

  let state = {
    notes: [],
    searchQuery: '',
    editingId: null,
    loaded: false,
    busy: false
  };

  const dom = {
    statusBanner: document.getElementById('status-banner'),
    searchInput: document.getElementById('search-input'),
    formHeading: document.getElementById('form-heading'),
    noteForm: document.getElementById('note-form'),
    noteId: document.getElementById('note-id'),
    noteTitle: document.getElementById('note-title'),
    noteContent: document.getElementById('note-content'),
    noteUrl: document.getElementById('note-url'),
    btnSubmit: document.getElementById('btn-submit'),
    btnCancel: document.getElementById('btn-cancel'),
    btnSaveTab: document.getElementById('btn-save-tab'),
    notesCount: document.getElementById('notes-count'),
    notesList: document.getElementById('notes-list'),
    emptyState: document.getElementById('empty-state')
  };

  let statusTimer;
  function showStatus(message, isError) {
    clearTimeout(statusTimer);
    dom.statusBanner.textContent = message;
    dom.statusBanner.className = 'status-banner ' + (isError ? 'error' : 'success');
    dom.statusBanner.classList.remove('hidden');
    if (!isError) statusTimer = setTimeout(() => {
      dom.statusBanner.classList.add('hidden');
    }, 4000);
  }

  function setBusy(busy) {
    state.busy = busy;
    const disabled = busy || !state.loaded;
    [dom.noteTitle, dom.noteContent, dom.noteUrl, dom.btnSubmit, dom.btnCancel,
      dom.btnSaveTab].forEach(el => { el.disabled = disabled; });
  }

  function loadNotesFromStorage() {
    if (!chrome.storage || !chrome.storage.local) {
      showStatus('Không thể truy cập chrome.storage.local.', true);
      return;
    }

    chrome.storage.local.get([STORAGE_KEY], function (result) {
      if (chrome.runtime.lastError) {
        showStatus('Lỗi khi đọc bộ nhớ: ' + chrome.runtime.lastError.message, true);
        return;
      }
      const stored = result[STORAGE_KEY] === undefined ? [] : result[STORAGE_KEY];
      const ids = new Set();
      if (!Array.isArray(stored) || stored.length > core.MAX_NOTES || stored.some(note => {
        if (!note || typeof note.id !== 'string' || !note.id || ids.has(note.id) ||
            ['title', 'content', 'url'].some(key => typeof note[key] !== 'string') ||
            !core.validateNoteInput(note).valid) return true;
        ids.add(note.id);
        return false;
      })) {
        showStatus('Dữ liệu lưu trữ không hợp lệ. Đã khóa ghi để bảo vệ ghi chú hiện có.', true);
        return;
      }
      state.notes = stored;
      state.loaded = true;
      setBusy(false);
      renderNotes();
    });
  }

  function saveNotesToStorage(nextNotes, callback) {
    if (!state.loaded || state.busy) return;
    if (!chrome.storage || !chrome.storage.local) {
      showStatus('Không thể lưu: thiếu quyền storage.', true);
      return;
    }

    if (nextNotes.length > core.MAX_NOTES) {
      showStatus('Đã vượt quá giới hạn tối đa ' + core.MAX_NOTES + ' ghi chú.', true);
      return;
    }

    const payload = {};
    payload[STORAGE_KEY] = nextNotes;
    setBusy(true);

    try { chrome.storage.local.set(payload, function () {
      setBusy(false);
      if (chrome.runtime.lastError) {
        showStatus('Lỗi lưu trữ: ' + chrome.runtime.lastError.message, true);
        return;
      }
      state.notes = nextNotes;
      if (typeof callback === 'function') callback();
    }); } catch (error) {
      setBusy(false);
      showStatus('Lỗi lưu trữ: ' + error.message, true);
    }
  }

  function resetForm() {
    if (state.busy) return;
    state.editingId = null;
    dom.noteId.value = '';
    dom.noteTitle.value = '';
    dom.noteContent.value = '';
    dom.noteUrl.value = '';
    dom.formHeading.textContent = 'Tạo ghi chú mới';
    dom.btnSubmit.textContent = 'Lưu ghi chú';
    dom.btnCancel.classList.add('hidden');
  }

  function populateFormForEdit(note) {
    if (!state.loaded || state.busy) return;
    state.editingId = note.id;
    dom.noteId.value = note.id;
    dom.noteTitle.value = note.title || '';
    dom.noteContent.value = note.content || '';
    dom.noteUrl.value = note.url || '';
    dom.formHeading.textContent = 'Chỉnh sửa ghi chú';
    dom.btnSubmit.textContent = 'Cập nhật';
    dom.btnCancel.classList.remove('hidden');
    dom.noteTitle.focus();
  }

  function renderNotes() {
    const filtered = core.filterNotes(state.notes, state.searchQuery);
    dom.notesCount.textContent = filtered.length + ' ghi chú';
    dom.notesList.textContent = '';

    if (filtered.length === 0) {
      dom.emptyState.textContent = state.searchQuery.trim()
        ? 'Không tìm thấy ghi chú phù hợp.' : 'Chưa có ghi chú nào. Hãy tạo mới hoặc lưu tab đang mở!';
      dom.emptyState.classList.remove('hidden');
      return;
    }

    dom.emptyState.classList.add('hidden');

    filtered.forEach(function (note) {
      const li = document.createElement('li');
      li.className = 'note-item';

      const header = document.createElement('div');
      header.className = 'note-item-header';

      const title = document.createElement('h3');
      title.className = 'note-item-title';
      title.textContent = note.title;
      header.appendChild(title);

      const actions = document.createElement('div');
      actions.className = 'note-item-actions';

      const btnEdit = document.createElement('button');
      btnEdit.type = 'button';
      btnEdit.className = 'btn btn-ghost btn-small';
      btnEdit.textContent = 'Sửa';
      btnEdit.addEventListener('click', function () {
        populateFormForEdit(note);
      });
      actions.appendChild(btnEdit);

      const btnDelete = document.createElement('button');
      btnDelete.type = 'button';
      btnDelete.className = 'btn btn-ghost btn-small';
      btnDelete.textContent = 'Xóa';
      btnDelete.addEventListener('click', function () {
        deleteNote(note.id);
      });
      actions.appendChild(btnDelete);

      header.appendChild(actions);
      li.appendChild(header);

      if (note.content) {
        const content = document.createElement('p');
        content.className = 'note-item-content';
        content.textContent = note.content;
        li.appendChild(content);
      }

      if (note.url && core.isValidUrl(note.url)) {
        const linkContainer = document.createElement('div');
        linkContainer.className = 'note-item-link-container';

        const linkBtn = document.createElement('button');
        linkBtn.type = 'button';
        linkBtn.className = 'note-item-link';
        linkBtn.textContent = '🔗 ' + note.url;
        linkBtn.title = 'Mở liên kết trong tab mới';
        linkBtn.addEventListener('click', function () {
          chrome.tabs.create({ url: note.url });
        });
        linkContainer.appendChild(linkBtn);
        li.appendChild(linkContainer);
      }

      const footer = document.createElement('div');
      footer.className = 'note-item-footer';
      const timeSpan = document.createElement('span');
      timeSpan.textContent = core.formatDisplayDate(note.updatedAt || note.createdAt);
      footer.appendChild(timeSpan);
      li.appendChild(footer);

      dom.notesList.appendChild(li);
    });
  }

  function deleteNote(id) {
    if (!state.loaded || state.busy) return;
    const nextNotes = state.notes.filter((n) => n.id !== id);
    saveNotesToStorage(nextNotes, function () {
      if (state.editingId === id) resetForm();
      showStatus('Đã xóa ghi chú thành công.', false);
      renderNotes();
    });
  }

  function handleFormSubmit(event) {
    event.preventDefault();
    if (!state.loaded || state.busy) return;

    const rawInput = {
      title: dom.noteTitle.value,
      content: dom.noteContent.value,
      url: dom.noteUrl.value
    };

    try {
      if (state.editingId) {
        const index = state.notes.findIndex((n) => n.id === state.editingId);
        if (index === -1) {
          throw new Error('Không tìm thấy ghi chú để cập nhật.');
        }
        const nextNotes = state.notes.slice();
        nextNotes[index] = core.updateNote(state.notes[index], rawInput);
        saveNotesToStorage(nextNotes, function () {
          showStatus('Đã cập nhật ghi chú.', false);
          resetForm();
          renderNotes();
        });
      } else {
        const newNote = core.createNote(rawInput);
        saveNotesToStorage([newNote, ...state.notes], function () {
          showStatus('Đã lưu ghi chú mới.', false);
          resetForm();
          renderNotes();
        });
      }
    } catch (err) {
      showStatus(err.message, true);
    }
  }

  function handleSaveCurrentTab() {
    if (!state.loaded || state.busy) return;
    if (!chrome.tabs || !chrome.tabs.query) {
      showStatus('Không thể lấy thông tin tab: thiếu quyền activeTab.', true);
      return;
    }

    setBusy(true);
    chrome.tabs.query({ active: true, currentWindow: true }, function (tabs) {
      setBusy(false);
      if (chrome.runtime.lastError || !tabs || tabs.length === 0) {
        showStatus('Không thể truy xuất tab hiện tại.', true);
        return;
      }

      const activeTab = tabs[0];
      const tabUrl = activeTab.url || '';
      const tabTitle = activeTab.title || 'Tab đã lưu';

      if (!core.isValidUrl(tabUrl)) {
        showStatus('Chỉ hỗ trợ lưu liên kết http/https (không lưu trang hệ thống).', true);
        return;
      }

      resetForm();
      dom.noteTitle.value = tabTitle.slice(0, core.MAX_TITLE_LENGTH);
      dom.noteUrl.value = tabUrl;
      dom.noteContent.focus();
      showStatus('Đã điền thông tin từ tab hiện tại. Hãy nhấn Lưu!', false);
    });
  }

  function init() {
    setBusy(false);
    dom.noteForm.addEventListener('submit', handleFormSubmit);
    dom.btnCancel.addEventListener('click', resetForm);
    dom.btnSaveTab.addEventListener('click', handleSaveCurrentTab);
    dom.searchInput.addEventListener('input', function (e) {
      state.searchQuery = e.target.value;
      renderNotes();
    });

    loadNotesFromStorage();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
