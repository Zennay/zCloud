import test from 'node:test';
import assert from 'node:assert/strict';
import {drawSlide, pickLayout, wrapText} from '../slides.js';

function fakeCtx() {
  const calls = [];
  const ctx = {calls, font: '', fillStyle: '', textAlign: 'left',
    measureText: t => ({width: String(t).length * 18}),
    fillText: (t, x, y) => calls.push(['text', String(t), x, y]),
    fillRect: (x, y, w, h) => calls.push(['rect', x, y, w, h]),
    beginPath() {}, arc: (x, y, r) => calls.push(['arc', x, y, r]), fill() {}};
  return ctx;
}
const texts = ctx => ctx.calls.filter(c => c[0] === 'text').map(c => c[1]);

test('layout picking is safe and backwards compatible', () => {
  assert.equal(pickLayout({title: 'a', body: ''}), 'legacy');
  assert.equal(pickLayout({layout: 'bogus', title: 'a'}), 'legacy');
  assert.equal(pickLayout({layout: 'quote'}), 'quote');
  assert.equal(pickLayout({stat: {value: '1', label: 'x'}}), 'stat');
  assert.equal(pickLayout({data: [{label: 'a', value: 1}]}), 'split');
});

test('wrapText wraps by measured width', () => {
  const rows = wrapText(fakeCtx(), 'aaa bbb ccc ddd', 100);
  assert.ok(rows.length > 1);
});

test('every layout draws without throwing and shows its content', () => {
  const slides = [
    {title: 'Welkom', body: 'Ondertitel', layout: 'title'},
    {title: 'Punten', body: 'een\ntwee\ndrie', layout: 'bullets', accent: 'teal'},
    {title: 'Cijfers', body: 'toelichting', layout: 'stat', stat: {value: '48', label: 'tests'}},
    {title: 'Cijfers', body: '', layout: 'stat', stat: {value: '48', label: 'tests'}, data: [{label: 'ok', value: 48}, {label: 'fout', value: 0}]},
    {title: 'Quote', body: 'Een mooie zin', layout: 'quote', quote_by: 'Iemand'},
    {title: 'Splits', body: 'a\nb', layout: 'split', data: [{label: 'x', value: 5}]},
    {title: 'Stappen', body: 'eerst\ndan\ndaarna', layout: 'bullets', visual: 'steps'},
    {title: 'Oud', body: 'Gewone tekst zoals voorheen'},
  ];
  slides.forEach((s, i) => {
    const ctx = fakeCtx();
    drawSlide(ctx, s, i, slides.length);
    assert.ok(texts(ctx).includes(s.title.split(' ')[0]) || texts(ctx).some(t => t.includes(s.title.split(' ')[0])), 'title drawn for ' + s.title);
    for (const c of ctx.calls) for (const n of c.slice(2)) if (typeof n === 'number') assert.ok(Number.isFinite(n));
  });
});

test('bars scale to the largest value and never go negative-width', () => {
  const ctx = fakeCtx();
  drawSlide(ctx, {title: 'T', body: '', layout: 'split', data: [{label: 'a', value: 100}, {label: 'b', value: 0}, {label: 'c', value: -50}]}, 0, 1);
  const bars = ctx.calls.filter(c => c[0] === 'rect' && c[4] === 22);
  assert.equal(bars.length, 3);
  assert.ok(bars.every(b => b[3] >= 6));
  assert.ok(bars[0][3] > bars[2][3]);
});

test('slide index is shown as 01 / 08', () => {
  const ctx = fakeCtx();
  drawSlide(ctx, {title: 'T', body: ''}, 0, 8);
  assert.ok(texts(ctx).includes('01 / 08'));
});
