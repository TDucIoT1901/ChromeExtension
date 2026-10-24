const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const core = require('../extension/core.js');

function harness(stored = [], deferLoad = false) {
  class Element {
    constructor() { this.children = []; this.events = {}; this.value = ''; this.disabled = false; this._text = ''; this.className = ''; this.classList = {add() {}, remove() {}, contains() {return false;}}; }
    set textContent(v) { this._text = v; this.children = []; }
    get textContent() { return this._text; }
    appendChild(v) { this.children.push(v); return v; }
    addEventListener(k, f) { this.events[k] = f; }
    setAttribute(k, v) { this[k] = v; }
    focus() {}
    reset() {}
    click() { if (!this.disabled) this.events.click?.({preventDefault() {}}); }
  }
  const elements = new Map();
  const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  let init, read;
  const writes = [];
  const chrome = {runtime: {}, storage: {local: {
    get(keys, cb) { read = () => cb({pocket_notes_data: stored}); if (!deferLoad) read(); },
    set(data, cb) { writes.push({data: structuredClone(data), cb}); }
  }}, tabs: {query(q, cb) {cb([{title: 'New tab', url: 'https://example.org/'}]);}, create() {}}};
  const document = {getElementById: get, createElement: () => new Element(),
    addEventListener(k, f) {if (k === 'DOMContentLoaded') init = f;},
    querySelectorAll() {return [...elements.values()];}};
  const context = {window: {PocketNotesCore: core}, document, chrome, setTimeout() {return 1;}, clearTimeout() {}, URL, console};
  vm.runInNewContext(fs.readFileSync(require.resolve('../extension/popup.js'), 'utf8'), context);
  init();
  return {get, writes, chrome, load: () => read(), submit(title = 'Test') {
    get('note-title').value = title;
    get('note-form').events.submit({preventDefault() {}});
  }, finish(error = false) {
    const last = writes.at(-1);
    chrome.runtime.lastError = error ? {message: 'storage failed'} : undefined;
    last.cb();
    chrome.runtime.lastError = undefined;
  }};
}

test('failed save can be retried without duplicating the note', () => {
  const h = harness(); h.submit(); h.finish(true); h.submit();
  assert.equal(h.writes.at(-1).data.pocket_notes_data.length, 1);
});
test('submitting before initial storage load cannot overwrite existing notes', () => {
  const h = harness([core.createNote({title: 'Existing'})], true);
  h.submit(); assert.equal(h.writes.length, 0); h.load();
});
test('rapid submit issues only one pending storage write', () => {
  const h = harness(); h.submit(); h.submit(); assert.equal(h.writes.length, 1);
});
test('malformed stored records are reported without enabling destructive overwrite', () => {
  const h = harness([null]); h.submit(); assert.equal(h.writes.length, 0);
  assert.match(h.get('status-banner').textContent, /lỗi|hợp lệ|hỏng/i);
});
test('a full collection rejects an extra note without corrupting memory', () => {
  const h = harness(Array.from({length: 300}, (_, i) => core.createNote({title: 'Note ' + i})));
  h.submit(); assert.equal(h.writes.length, 0);
  assert.match(h.get('status-banner').textContent, /300|giới hạn/i);
});
test('script text in a note remains literal text', () => {
  const h = harness([core.createNote({title: '<img src=x onerror=alert(1)>'})]);
  assert.equal(h.get('notes-list').children[0].children[0].children[0].textContent, '<img src=x onerror=alert(1)>');
});

test('failed delete preserves the existing note and edit state', () => {
  const h = harness([core.createNote({title: 'Keep me'})]);
  const actions = h.get('notes-list').children[0].children[0].children[1];
  actions.children[0].click();
  actions.children[1].click();
  h.finish(true);
  h.submit('Updated');
  assert.equal(h.writes.at(-1).data.pocket_notes_data.length, 1);
  assert.equal(h.writes.at(-1).data.pocket_notes_data[0].title, 'Updated');
});

test('saving a tab while editing creates a new note rather than replacing the old one', () => {
  const h = harness([core.createNote({title: 'Original'})]);
  h.get('notes-list').children[0].children[0].children[1].children[0].click();
  h.get('btn-save-tab').click();
  h.submit('New tab');
  assert.equal(h.writes.at(-1).data.pocket_notes_data.length, 2);
  assert.equal(h.writes.at(-1).data.pocket_notes_data[1].title, 'Original');
});
