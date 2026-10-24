(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.PocketNotesCore = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const MAX_TITLE_LENGTH = 120;
  const MAX_CONTENT_LENGTH = 4000;
  const MAX_NOTES = 300;

  function isValidUrl(urlString) {
    if (typeof urlString !== 'string') return false;
    const trimmed = urlString.trim();
    if (!trimmed) return false;
    try {
      const parsed = new URL(trimmed);
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch (e) {
      return false;
    }
  }

  function validateNoteInput(input) {
    if (!input || typeof input !== 'object') {
      return { valid: false, error: 'Dữ liệu đầu vào không hợp lệ.' };
    }

    const title = typeof input.title === 'string' ? input.title.trim() : '';
    const content = typeof input.content === 'string' ? input.content.trim() : '';
    const rawUrl = typeof input.url === 'string' ? input.url.trim() : '';

    if (!title) {
      return { valid: false, error: 'Tiêu đề không được để trống.' };
    }

    if (title.length > MAX_TITLE_LENGTH) {
      return { valid: false, error: 'Tiêu đề không được vượt quá ' + MAX_TITLE_LENGTH + ' ký tự.' };
    }

    if (content.length > MAX_CONTENT_LENGTH) {
      return { valid: false, error: 'Nội dung không được vượt quá ' + MAX_CONTENT_LENGTH + ' ký tự.' };
    }

    if (rawUrl && !isValidUrl(rawUrl)) {
      return { valid: false, error: 'Liên kết phải thuộc giao thức hợp lệ (http hoặc https).' };
    }

    return {
      valid: true,
      sanitized: {
        title: title,
        content: content,
        url: rawUrl ? new URL(rawUrl).href : ''
      }
    };
  }

  function createNote(input) {
    const validation = validateNoteInput(input);
    if (!validation.valid) {
      throw new Error(validation.error);
    }

    const now = new Date().toISOString();
    const id = 'note_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);

    return {
      id: id,
      title: validation.sanitized.title,
      content: validation.sanitized.content,
      url: validation.sanitized.url,
      createdAt: now,
      updatedAt: now
    };
  }

  function updateNote(existingNote, input) {
    if (!existingNote || typeof existingNote.id !== 'string') {
      throw new Error('Ghi chú cần cập nhật không tồn tại.');
    }

    const validation = validateNoteInput(input);
    if (!validation.valid) {
      throw new Error(validation.error);
    }

    return {
      ...existingNote,
      title: validation.sanitized.title,
      content: validation.sanitized.content,
      url: validation.sanitized.url,
      updatedAt: new Date().toISOString()
    };
  }

  function filterNotes(notes, query) {
    if (!Array.isArray(notes)) return [];
    if (typeof query !== 'string') return notes.slice();

    const term = query.trim().toLowerCase();
    if (!term) return notes.slice();

    return notes.filter(function (note) {
      const titleMatch = typeof note.title === 'string' && note.title.toLowerCase().includes(term);
      const contentMatch = typeof note.content === 'string' && note.content.toLowerCase().includes(term);
      const urlMatch = typeof note.url === 'string' && note.url.toLowerCase().includes(term);
      return titleMatch || contentMatch || urlMatch;
    });
  }

  function formatDisplayDate(isoString) {
    if (!isoString) return '';
    try {
      const date = new Date(isoString);
      if (isNaN(date.getTime())) return '';
      const pad = (n) => String(n).padStart(2, '0');
      const hours = pad(date.getHours());
      const minutes = pad(date.getMinutes());
      const day = pad(date.getDate());
      const month = pad(date.getMonth() + 1);
      const year = date.getFullYear();
      return hours + ':' + minutes + ' ' + day + '/' + month + '/' + year;
    } catch (e) {
      return '';
    }
  }

  return {
    MAX_TITLE_LENGTH: MAX_TITLE_LENGTH,
    MAX_CONTENT_LENGTH: MAX_CONTENT_LENGTH,
    MAX_NOTES: MAX_NOTES,
    isValidUrl: isValidUrl,
    validateNoteInput: validateNoteInput,
    createNote: createNote,
    updateNote: updateNote,
    filterNotes: filterNotes,
    formatDisplayDate: formatDisplayDate
  };
});