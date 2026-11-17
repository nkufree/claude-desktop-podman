const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

const source = readFileSync(join(__dirname, '../webapp/static/desktop-storage.js'), 'utf8');

for (const mode of ['getter blocked', 'writes blocked']) {
  test(`noVNC preferences work when storage is ${mode}`, () => {
    const window = {};
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        if (mode === 'getter blocked') throw new Error('Access denied');
        return {setItem() { throw new Error('Access denied'); }};
      },
    });
    runInNewContext(source, {window});
    const storage = window.localStorage;
    assert.equal(storage.getItem('resize'), null);
    storage.setItem('resize', 'scale');
    storage.setItem('reconnect', true);
    assert.equal(storage.getItem('resize'), 'scale');
    assert.equal(storage.getItem('reconnect'), 'true');
    assert.equal(storage.length, 2);
    assert.equal(storage.key(0), 'resize');
    storage.removeItem('resize');
    assert.equal(storage.getItem('resize'), null);
    storage.clear();
    assert.equal(storage.length, 0);
    assert.equal(storage.key(0), null);
    const otherWindow = {};
    runInNewContext(source, {window: otherWindow});
    assert.notEqual(otherWindow.localStorage, storage);
  });
}

test('available site storage and existing preferences are preserved', () => {
  const values = new Map([['resize', 'scale']]);
  const storage = {
    setItem(key, value) { values.set(key, value); },
    removeItem(key) { values.delete(key); },
  };
  const window = {localStorage: storage};
  runInNewContext(source, {window});
  assert.equal(window.localStorage, storage);
  assert.equal(values.size, 1);
  assert.equal(values.get('resize'), 'scale');
});
