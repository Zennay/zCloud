// Draws one presentation slide on a 1536x864 canvas context. Pure drawing, no DOM: testable with a fake context.
export const SLIDE_W = 1536, SLIDE_H = 864;
const ACCENTS = {gold: '#b49662', teal: '#4d9aa0', rose: '#c97b8b', violet: '#9a86d1', slate: '#6c7f8c'};
const LAYOUTS = new Set(['title', 'bullets', 'stat', 'quote', 'split']);

export function wrapText(ctx, text, max) {
  const lines = [];
  for (const paragraph of String(text || '').split('\n')) {
    let line = '';
    for (const word of paragraph.split(' ')) {
      const candidate = line ? line + ' ' + word : word;
      if (ctx.measureText(candidate).width > max && line) { lines.push(line); line = word; } else line = candidate;
    }
    lines.push(line);
  }
  return lines;
}

export function pickLayout(slide) {
  if (LAYOUTS.has(slide.layout)) return slide.layout;
  if (slide.stat) return 'stat';
  if (slide.data && slide.data.length) return 'split';
  return 'legacy';
}

export function drawSlide(ctx, slide, index, total, brand = 'Z C L O U D   /   T H E   B O A R D R O O M') {
  const dark = index % 2 === 1, accent = ACCENTS[slide.accent] || (dark ? '#cbb088' : '#756044');
  const ink = dark ? '#eee5d5' : '#23343b', muted = dark ? '#b5c4be' : '#5d6d70';
  const layout = pickLayout(slide), lines = String(slide.body || '').split('\n').map(s => s.trim()).filter(Boolean);
  ctx.fillStyle = dark ? '#20353e' : '#e7e0d3'; ctx.fillRect(0, 0, SLIDE_W, SLIDE_H);
  ctx.fillStyle = accent; ctx.fillRect(0, 0, 14, SLIDE_H);
  ctx.fillStyle = accent; ctx.font = '23px sans-serif'; ctx.textAlign = 'left'; ctx.fillText(brand, 110, 100);
  ctx.fillStyle = muted; ctx.font = '23px sans-serif'; ctx.textAlign = 'right';
  ctx.fillText(String(index + 1).padStart(2, '0') + ' / ' + String(total).padStart(2, '0'), 1440, 780); ctx.textAlign = 'left';

  const title = (x, y, max, size = 64) => {
    ctx.fillStyle = ink; ctx.font = size + 'px Georgia';
    const rows = wrapText(ctx, slide.title, max).slice(0, 2);
    rows.forEach((l, i) => ctx.fillText(l, x, y + i * (size + 10)));
    return y + rows.length * (size + 10);
  };
  const bullets = (x, y, max, maxRows = 6) => {
    ctx.font = '34px sans-serif';
    let row = 0;
    for (const item of lines) {
      for (const [i, l] of wrapText(ctx, item, max - 40).entries()) {
        if (row >= maxRows + 2) return;
        if (i === 0) { ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(x + 8, y + row * 50 - 11, 6, 0, Math.PI * 2); ctx.fill(); }
        ctx.fillStyle = ink; ctx.fillText(l, x + 36, y + row * 50); row++;
      }
    }
  };

  if (layout === 'title') {
    ctx.fillStyle = ink; ctx.font = '96px Georgia';
    wrapText(ctx, slide.title, 1250).slice(0, 3).forEach((l, i) => ctx.fillText(l, 110, 360 + i * 108));
    ctx.fillStyle = accent; ctx.fillRect(110, 640, 120, 6);
    ctx.fillStyle = muted; ctx.font = '36px sans-serif'; ctx.fillText(lines[0] || '', 110, 700);
  } else if (layout === 'quote') {
    ctx.fillStyle = muted; ctx.font = '34px sans-serif'; ctx.fillText(wrapText(ctx, slide.title, 1250)[0], 110, 170);
    ctx.fillStyle = accent; ctx.font = '240px Georgia'; ctx.fillText('“', 90, 330);
    ctx.fillStyle = ink; ctx.font = 'italic 54px Georgia';
    wrapText(ctx, slide.body, 1150).slice(0, 5).forEach((l, i) => ctx.fillText(l, 200, 300 + i * 70));
    if (slide.quote_by) { ctx.fillStyle = muted; ctx.font = '32px sans-serif'; ctx.fillText('— ' + slide.quote_by, 200, 700); }
  } else if (layout === 'stat') {
    title(110, 230, 1250, 56);
    ctx.fillStyle = accent; ctx.font = '210px Georgia';
    const value = slide.stat ? slide.stat.value : '';
    ctx.fillText(value, 110, 560);
    ctx.fillStyle = muted; ctx.font = '34px sans-serif'; ctx.fillText(slide.stat ? slide.stat.label : '', 116, 625);
    const x = ctx.measureText ? Math.max(700, 150 + ctx.measureText(value).width) : 760;
    if (slide.data && slide.data.length) drawBars(ctx, slide.data, x, 330, 1440 - x, accent, ink, muted);
    else { ctx.font = '34px sans-serif'; bullets(Math.min(x, 900), 360, 1440 - Math.min(x, 900), 5); }
  } else if (layout === 'split' || (layout === 'bullets' && slide.data && slide.data.length)) {
    title(110, 230, 1250, 56);
    bullets(110, 360, 640, 6);
    if (slide.visual === 'steps') drawSteps(ctx, lines, 860, 330, accent, ink);
    else if (slide.data && slide.data.length) drawBars(ctx, slide.data, 830, 340, 600, accent, ink, muted);
  } else if (layout === 'bullets') {
    title(110, 230, 1250, 60);
    if (slide.visual === 'steps') drawSteps(ctx, lines, 150, 360, accent, ink, true);
    else bullets(110, 370, 1250, 7);
  } else { // legacy title + paragraph layout
    const end = title(110, 270, 1270, 72);
    ctx.fillStyle = ink; ctx.font = '33px sans-serif';
    wrapText(ctx, slide.body, 1240).slice(0, 7).forEach((l, i) => ctx.fillText(l, 110, end + 60 + i * 45));
  }
}

export function drawBars(ctx, data, x, y, width, accent, ink, muted) {
  const max = Math.max(...data.map(d => Math.abs(d.value)), 1), rowH = 74;
  data.slice(0, 8).forEach((d, i) => {
    const top = y + i * rowH, w = Math.max(6, Math.abs(d.value) / max * (width - 190));
    ctx.fillStyle = muted; ctx.font = '26px sans-serif'; ctx.fillText(String(d.label), x, top);
    ctx.fillStyle = accent; ctx.fillRect(x, top + 14, w, 22);
    ctx.fillStyle = ink; ctx.font = '26px sans-serif'; ctx.fillText(String(d.value), x + w + 14, top + 34);
  });
}

export function drawSteps(ctx, lines, x, y, accent, ink, horizontal = false) {
  const items = lines.slice(0, 5);
  items.forEach((text, i) => {
    const cx = horizontal ? x + i * 250 : x, cy = horizontal ? y + 40 : y + i * 90;
    ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(cx + 28, cy, 28, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#ffffff'; ctx.font = '30px sans-serif'; ctx.textAlign = 'center'; ctx.fillText(String(i + 1), cx + 28, cy + 11); ctx.textAlign = 'left';
    ctx.fillStyle = ink; ctx.font = '30px sans-serif';
    if (horizontal) wrapText(ctx, text, 200).slice(0, 4).forEach((l, r) => ctx.fillText(l, cx, cy + 80 + r * 38));
    else ctx.fillText(text.slice(0, 40), cx + 80, cy + 11);
  });
}
