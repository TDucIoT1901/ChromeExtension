(function () {
  'use strict';

  const core = window.PocketNotesCore;
  const ACTIONS = core.ACTIONS || {
    LIST_NOTES: 'notes:list',
    CREATE_NOTE: 'notes:create',
    UPDATE_NOTE: 'notes:update',
    DELETE_NOTE: 'notes:delete'
  };

  let state = {
    notes: [],
    searchQuery: '',
    editingId: null,
    editingSnapshot: null,
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

  function sendWorkerRequest(payload, onSuccess, onFailure) {
    if (typeof chrome === 'undefined' || !chrome.runtime ||
        typeof chrome.runtime.sendMessage !== 'function') {
      const msg = 'Không thể kết nối dịch vụ nền của tiện ích.';
      if (typeof onFailure === 'function') onFailure(msg);
      else showStatus(msg, true);
      return;
    }

    setBusy(true);

    try {
      chrome.runtime.sendMessage(payload, function (response) {
        setBusy(false);

        if (chrome.runtime && chrome.runtime.lastError) {
          const msg = 'Lỗi giao tiếp dịch vụ nền: ' + chrome.runtime.lastError.message;
          if (typeof onFailure === 'function') onFailure(msg);
          else showStatus(msg, true);
          return;
        }

        if (!response || typeof response !== 'object') {
          const msg = 'Phản hồi từ dịch vụ nền không hợp lệ.';
          if (typeof onFailure === 'function') onFailure(msg);
          else showStatus(msg, true);
          return;
        }

        if (response.ok !== true) {
          const msg = (typeof response.error === 'string' && response.error)
            ? response.error
            : 'Không thể thực hiện thao tác với bộ nhớ.';
          if (typeof onFailure === 'function') onFailure(msg, response);
          else showStatus(msg, true);
          return;
        }

        const validation = core.validateStoredNotes(response.notes);
        if (!validation.valid) {
          if (typeof onFailure === 'function') onFailure(validation.error, response);
          else showStatus(validation.error, true);
          return;
        }

        onSuccess(validation.notes, response);
      });
    } catch (error) {
      setBusy(false);
      const msg = 'Lỗi giao tiếp dịch vụ nền: ' + (error && error.message ? error.message : String(error));
      if (typeof onFailure === 'function') onFailure(msg);
      else showStatus(msg, true);
    }
  }

  function loadNotesFromBackground() {
    sendWorkerRequest(
      { type: ACTIONS.LIST_NOTES },
      function (validatedNotes) {
        state.notes = validatedNotes;
        state.loaded = true;
        setBusy(false);
        renderNotes();
      },
      function (errorMsg) {
        state.loaded = false;
        setBusy(false);
        showStatus(errorMsg, true);
      }
    );
  }

  function resetForm() {
    if (state.busy) return;
    state.editingId = null;
    state.editingSnapshot = null;
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
    if (!core.validateStoredNote(note)) {
      showStatus('Ghi chú cần chỉnh sửa không hợp lệ.', true);
      return;
    }
    state.editingId = note.id;
    state.editingSnapshot = core.cloneNote(note);
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
      const noteSnapshot = core.cloneNote(note);
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
        populateFormForEdit(noteSnapshot);
      });
      actions.appendChild(btnEdit);

      const btnDelete = document.createElement('button');
      btnDelete.type = 'button';
      btnDelete.className = 'btn btn-ghost btn-small';
      btnDelete.textContent = 'Xóa';
      btnDelete.addEventListener('click', function () {
        deleteNote(noteSnapshot);
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

  function deleteNote(noteOrId) {
    if (!state.loaded || state.busy) return;
    const snapshot = (noteOrId && typeof noteOrId === 'object')
      ? core.cloneNote(noteOrId)
      : core.cloneNote(state.notes.find(function (n) { return n.id === noteOrId; }));

    if (!snapshot || !core.validateStoredNote(snapshot)) {
      showStatus('Không tìm thấy ghi chú hợp lệ để xóa.', true);
      return;
    }

    sendWorkerRequest(
      {
        type: ACTIONS.DELETE_NOTE,
        id: snapshot.id,
        snapshot: snapshot
      },
      function (nextNotes) {
        state.notes = nextNotes;
        if (state.editingId === snapshot.id) {
          resetForm();
        }
        showStatus('Đã xóa ghi chú thành công.', false);
        renderNotes();
      }
    );
  }

  function handleFormSubmit(event) {
    event.preventDefault();
    if (!state.loaded || state.busy) return;

    const rawInput = {
      title: dom.noteTitle.value,
      content: dom.noteContent.value,
      url: dom.noteUrl.value
    };

    const validation = core.validateNoteInput(rawInput);
    if (!validation.valid) {
      showStatus(validation.error, true);
      return;
    }

    if (state.editingId) {
      const snapshot = (state.editingSnapshot && state.editingSnapshot.id === state.editingId)
        ? core.cloneNote(state.editingSnapshot)
        : core.cloneNote(state.notes.find(function (n) { return n.id === state.editingId; }));

      if (!snapshot || !core.validateStoredNote(snapshot)) {
        showStatus('Không tìm thấy ghi chú để cập nhật.', true);
        return;
      }

      sendWorkerRequest(
        {
          type: ACTIONS.UPDATE_NOTE,
          id: state.editingId,
          snapshot: snapshot,
          input: validation.sanitized
        },
        function (nextNotes) {
          state.notes = nextNotes;
          showStatus('Đã cập nhật ghi chú.', false);
          resetForm();
          renderNotes();
        }
      );
    } else {
      sendWorkerRequest(
        {
          type: ACTIONS.CREATE_NOTE,
          input: validation.sanitized
        },
        function (nextNotes) {
          state.notes = nextNotes;
          showStatus('Đã lưu ghi chú mới.', false);
          resetForm();
          renderNotes();
        }
      );
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

    loadNotesFromBackground();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
