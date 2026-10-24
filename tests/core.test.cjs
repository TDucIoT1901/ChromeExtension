const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../extension/core.js');

test('isValidUrl verifies http/https and rejects dangerous or non-web schemes', () => {
  assert.equal(core.isValidUrl('https://example.com'), true);
  assert.equal(core.isValidUrl('http://localhost:3000/path?query=1'), true);
  assert.equal(core.isValidUrl('javascript:alert(1)'), false);
  assert.equal(core.isValidUrl('data:text/html,<b>xss</b>'), false);
  assert.equal(core.isValidUrl('file:///etc/passwd'), false);
  assert.equal(core.isValidUrl(''), false);
  assert.equal(core.isValidUrl(null), false);
  assert.equal(core.isValidUrl('not-a-valid-url'), false);
});

test('validateNoteInput validates required fields and length constraints', () => {
  const validResult = core.validateNoteInput({
    title: '  Học Tiếng Anh  ',
    content: 'Ôn tập 20 từ vựng',
    url: 'https://dictionary.cambridge.org'
  });
  assert.equal(validResult.valid, true);
  assert.equal(validResult.sanitized.title, 'Học Tiếng Anh');
  assert.equal(validResult.sanitized.url, 'https://dictionary.cambridge.org/');

  const emptyTitle = core.validateNoteInput({ title: '   ', content: 'Nội dung' });
  assert.equal(emptyTitle.valid, false);
  assert.match(emptyTitle.error, /Tiêu đề không được để trống/);

  const tooLongTitle = core.validateNoteInput({ title: 'a'.repeat(121) });
  assert.equal(tooLongTitle.valid, false);

  const invalidUrl = core.validateNoteInput({ title: 'Tựa đề', url: 'ftp://files.example.com' });
  assert.equal(invalidUrl.valid, false);
});

test('createNote generates note object with id and timestamps', () => {
  const note = core.createNote({
    title: 'Ghi chú kiểm tra',
    content: 'Nội dung mẫu',
    url: 'https://developer.chrome.com'
  });

  assert.ok(note.id.startsWith('note_'));
  assert.equal(note.title, 'Ghi chú kiểm tra');
  assert.equal(note.content, 'Nội dung mẫu');
  assert.equal(note.url, 'https://developer.chrome.com/');
  assert.ok(note.createdAt);
  assert.equal(note.createdAt, note.updatedAt);
});

test('updateNote updates content and refreshes updatedAt', async () => {
  const original = core.createNote({ title: 'Ban đầu', content: 'Nội dung 1' });
  await new Promise((r) => setTimeout(r, 10));

  const updated = core.updateNote(original, {
    title: 'Đã sửa',
    content: 'Nội dung 2',
    url: 'https://example.org'
  });

  assert.equal(updated.id, original.id);
  assert.equal(updated.title, 'Đã sửa');
  assert.equal(updated.content, 'Nội dung 2');
  assert.equal(updated.createdAt, original.createdAt);
  assert.ok(new Date(updated.updatedAt).getTime() >= new Date(original.updatedAt).getTime());
});

test('filterNotes filters by title, content, and url with case insensitivity', () => {
  const sampleNotes = [
    { id: '1', title: 'Họp nhóm Sprint', content: 'Thảo luận kiến trúc', url: 'https://meet.google.com' },
    { id: '2', title: 'Mua sắm cuối tuần', content: 'Mua sữa và táo', url: '' },
    { id: '3', title: 'Đọc sách Clean Code', content: 'Chương 5: Formatting', url: 'https://clean-code.io' }
  ];

  const matchedByTitle = core.filterNotes(sampleNotes, 'sprint');
  assert.equal(matchedByTitle.length, 1);
  assert.equal(matchedByTitle[0].id, '1');

  const matchedByContent = core.filterNotes(sampleNotes, 'sữa');
  assert.equal(matchedByContent.length, 1);
  assert.equal(matchedByContent[0].id, '2');

  const matchedByUrl = core.filterNotes(sampleNotes, 'clean-code');
  assert.equal(matchedByUrl.length, 1);
  assert.equal(matchedByUrl[0].id, '3');

  const emptyQuery = core.filterNotes(sampleNotes, '   ');
  assert.equal(emptyQuery.length, 3);
});

test('formatDisplayDate handles valid and invalid date inputs safely', () => {
  assert.equal(core.formatDisplayDate('invalid-date'), '');
  assert.equal(core.formatDisplayDate(null), '');
  const formatted = core.formatDisplayDate('2026-10-24T08:30:00.000Z');
  assert.match(formatted, /\d{2}:\d{2} \d{2}\/\d{2}\/\d{4}/);
});