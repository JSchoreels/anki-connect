// Runs only on the disposable benchmark origin. The user's reader storage is separate.
import loadEpub from '/src/lib/functions/file-loaders/epub/load-epub.ts';
import { createBooksDb } from '/src/lib/data/database/books-db/factory.ts';

const config = await (await fetch('/__bench/config')).json();
const params = new URLSearchParams(location.search);
if (params.get('suite') === 'frontend') {
  const runs = [];
  for (let pair = 1; pair <= 5; pair++) {
    for (const reader of pair % 2 ? ['original', 'optimized'] : ['optimized', 'original']) {
      for (const cache of ['cold', 'warm']) runs.push({ reader, variant: 'live', cache, run: 'paired-' + pair });
    }
  }
  const first = runs.shift();
  sessionStorage.setItem('ebook-benchmark-queue', JSON.stringify(runs));
  for (const [key, value] of Object.entries(first)) params.set(key, value);
  params.set('auto', '1');
}
if (params.get('suite') === 'live') {
  const runs = [];
  for (let pair = 1; pair <= 5; pair++) {
    for (const cache of ['cold', 'warm']) runs.push({ variant: 'live', cache, run: (params.get('label') || 'live') + '-' + pair });
  }
  const first = runs.shift();
  sessionStorage.setItem('ebook-benchmark-queue', JSON.stringify(runs));
  for (const [key, value] of Object.entries(first)) params.set(key, value);
  params.set('auto', '1');
}
if (params.get('suite') === 'native') {
  const runs = [];
  for (let pair = 1; pair <= 5; pair++) {
    for (const variant of pair % 2 ? ['current', 'prototype'] : ['prototype', 'current']) {
      for (const cache of ['cold', 'warm']) runs.push({ variant, cache, run: 'pair-' + pair });
    }
  }
  if (config.live) for (let pair = 1; pair <= 3; pair++) {
    for (const cache of ['cold', 'warm']) runs.push({ variant: 'live', cache, run: 'live-' + pair });
  }
  const first = runs.shift();
  sessionStorage.setItem('ebook-benchmark-queue', JSON.stringify(runs));
  for (const [key, value] of Object.entries(first)) params.set(key, value);
  params.set('auto', '1');
}
const variant = params.get('variant') || 'current';
const reader = params.get('reader') || 'optimized';
if (!['original', 'optimized'].includes(reader)) throw new Error('Invalid reader variant');
document.cookie = 'ebook-benchmark-reader=' + reader + '; path=/; SameSite=Lax';
const cold = params.get('cache') !== 'warm';
if (!['current', 'prototype', 'live'].includes(variant) || !config[variant]) throw new Error('Invalid variant');
localStorage.setItem('ankiIntegrationEnabled', '1');
localStorage.setItem('ankiConnectUrl', config[variant]);
localStorage.setItem('ankiWordFields', JSON.stringify(['Front', 'Front_Kana']));
localStorage.setItem('ankiWordDeckNames', JSON.stringify(['Japan::1. Vocabulary']));
localStorage.setItem('ankiColorMode', 'combined');
localStorage.setItem('book-reader-token-panel-filter-v1', 'all');
localStorage.setItem('book-reader-token-panel-orthography-filter-v1', 'all-scripts');
localStorage.setItem('book-reader-token-panel-sort-v1', 'frequency');
localStorage.setItem('yomitanUrl', 'http://localhost:19633');
localStorage.setItem('lastStorageSource', 'browser');
localStorage.setItem('fsStorageSource', '');
if (cold) {
  await new Promise((resolve, reject) => {
    const request = indexedDB.deleteDatabase('anki-cache');
    request.onsuccess = resolve;
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error('Benchmark cache is still open'));
  });
  localStorage.removeItem('anki-warm-cache-version');
  localStorage.removeItem('anki-warm-cache-updated-at');
}
const db = await createBooksDb();
let book = await db.get('data', 1);
let importMs = null;
if (!book) {
  const file = new File([await (await fetch('/__bench/book.epub')).blob()], config.epub_name);
  const start = performance.now();
  book = await loadEpub(file, document, 1);
  importMs = performance.now() - start;
}
await db.put('data', { ...book, id: 1, storageSource: undefined });
db.close();
sessionStorage.setItem('ebook-benchmark-run', JSON.stringify({
  variant, reader, cache: cold ? 'cold' : 'warm', run: params.get('run'),
  title: book.title, characters: book.characters, importMs,
}));
document.querySelector('#status').textContent = JSON.stringify({
  ready: true, variant, cache: cold ? 'cold' : 'warm', title: book.title,
  characters: book.characters, importMs,
});
document.querySelector('#load-book').hidden = false;
if (params.get('auto') === '1') location.href = '/b?id=1';
