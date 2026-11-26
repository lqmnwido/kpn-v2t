export function mediaKind(file) {
  if (file.type.startsWith('video/')) return 'video';
  if (file.type.startsWith('audio/')) return 'audio';
  return /\.(mp4|webm|mkv|mov|avi|mpeg|m4v)$/i.test(file.name) ? 'video' : 'audio';
}

export function inspectMedia(file) {
  const kind = mediaKind(file);
  return new Promise(resolve => {
    const player = document.createElement(kind);
    const url = URL.createObjectURL(file);
    let duration = null;
    let timer;
    const done = (thumbnail = null) => {
      clearTimeout(timer);
      player.onloadedmetadata = null;
      player.onseeked = null;
      player.onloadeddata = null;
      player.onerror = null;
      player.removeAttribute('src');
      URL.revokeObjectURL(url);
      resolve({ kind, duration, thumbnail });
    };
    function capture() {
      if (!player.videoWidth || player.readyState < 2) return;
      const canvas = document.createElement('canvas');
      canvas.width = 128;
      canvas.height = Math.max(1, Math.round(128 * player.videoHeight / player.videoWidth));
      const context = canvas.getContext('2d');
      if (!context) return done();
      context.drawImage(player, 0, 0, canvas.width, canvas.height);
      done(canvas.toDataURL('image/jpeg', 0.7));
    }
    player.preload = kind === 'video' ? 'auto' : 'metadata';
    player.muted = true;
    player.onloadedmetadata = () => {
      if (Number.isFinite(player.duration)) duration = player.duration;
      if (kind === 'audio') done();
      else if (duration > 0.2) player.currentTime = Math.min(duration * 0.15, 2);
      else capture();
    };
    player.onloadeddata = () => {
      if (!player.seeking) capture();
    };
    player.onseeked = capture;
    player.onerror = () => done();
    timer = setTimeout(() => done(), 4500);
    player.src = url;
  });
}

export function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '—';
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total / 60) % 60;
  const remainder = String(total % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${remainder}` : `${minutes}:${remainder}`;
}

function timestamp(value) {
  return value.split(':').reduce((seconds, component) => seconds * 60 + Number(component), 0);
}

export function parseVtt(source) {
  const cues = [];
  const timestampPattern = /^(\d{2,}:)?\d{2}:\d{2}\.\d{3}$/;
  for (const block of source.trim().split(/(?:\r?\n){2,}/)) {
    const lines = block.split(/\r?\n/);
    if (/^(WEBVTT|NOTE|STYLE|REGION)(?:\s|$)/.test(lines[0])) continue;
    const timing = lines.findIndex(line => line.includes(' --> '));
    if (timing < 0) continue;
    const [start, end] = lines[timing].trim().split(/\s+-->\s+|\s+/);
    if (!timestampPattern.test(start) || !timestampPattern.test(end)) continue;
    const text = lines.slice(timing + 1).join('\n')
      .replace(/<v\s+([^>]+)>/g, '$1: ')
      .replace(/<[^>]+>/g, '')
      .replace(/&(amp|lt|gt|nbsp|lrm|rlm);/g, (_, entity) =>
        ({ amp: '&', lt: '<', gt: '>', nbsp: '\u00a0', lrm: '\u200e', rlm: '\u200f' })[entity]);
    if (text.trim()) cues.push({ start: timestamp(start), end: timestamp(end), text });
  }
  return cues.sort((left, right) => left.start - right.start);
}

export function parseCueTime(value) {
  const text = value.trim();
  if (/^\d+(?:\.\d{1,3})?$/.test(text)) return Number(text);
  if (!/^(?:\d+:)?\d{1,2}:[0-5]\d(?:\.\d{1,3})?$/.test(text)) return NaN;
  const parts = text.split(':');
  if (parts.length === 3 && Number(parts[1]) >= 60) return NaN;
  return timestamp(text);
}

export function formatVttTime(seconds) {
  const ms = Math.round(seconds * 1000);
  const hours = Math.floor(ms / 3600000);
  const minutes = Math.floor(ms / 60000) % 60;
  const secs = Math.floor(ms / 1000) % 60;
  const fraction = String(ms % 1000).padStart(3, '0');
  return [hours, minutes, secs].map(part => String(part).padStart(2, '0')).join(':') + '.' + fraction;
}

function cueBlockStart(block) {
  if (/^(NOTE|STYLE|REGION)(?:\s|$)/.test(block)) return NaN;
  const line = block.split('\n').slice(0, 2).find(line => line.includes(' --> '));
  if (!line) return NaN;
  return parseCueTime(line.trim().split(/\s+/)[0]);
}

export function appendVttCues(source, additions) {
  const blocks = source.replace(/\r\n/g, '\n').trimEnd().split(/\n[ \t]*\n+/);
  if (!/^\uFEFF?WEBVTT(?:\s|$)/.test(blocks[0])) throw new Error('Format fail VTT tidak sah.');
  const existing = new Set(blocks.slice(1).filter(block => Number.isFinite(cueBlockStart(block)))
    .map(block => block.split('\n')[0]));
  let added = false;
  for (const cue of [...additions].sort((a, b) => a.start - b.start)) {
    if (existing.has(cue.id)) continue;
    const label = cue.speaker ? `${cue.speaker}: ` : '';
    const text = (label + cue.text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const block = `${cue.id}\n${formatVttTime(cue.start)} --> ${formatVttTime(cue.end)}\n${text}`;
    const next = blocks.findIndex((candidate, index) => index > 0 && cueBlockStart(candidate) > cue.start);
    if (next < 0) blocks.push(block);
    else blocks.splice(next, 0, block);
    existing.add(cue.id);
    added = true;
  }
  return added ? blocks.join('\n\n') + '\n' : source;
}

function readCueBlock(block, index) {
  if (!Number.isFinite(cueBlockStart(block))) return null;
  const lines = block.split('\n');
  const timingIndex = lines[0].includes(' --> ') ? 0 : 1;
  const timing = lines[timingIndex];
  const [, end] = timing.trim().split(/\s+-->\s+|\s+/);
  return {
    index, id: timingIndex ? lines[0] : '', timing,
    start: cueBlockStart(block), end: parseCueTime(end),
    text: parseVtt(block)[0]?.text || '',
  };
}

const near = (left, right) => Math.abs(left - right) <= 0.002;
const normalized = text => text.replace(/\s+/g, ' ').trim();
const escapeCueText = text => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export function editVttCues(source, additions, updates) {
  const editedIds = new Set(updates.map(edit => edit.id));
  const withAdditions = appendVttCues(source, additions.filter(cue => !editedIds.has(cue.id)));
  if (!updates.length) return withAdditions;
  const blocks = withAdditions.replace(/\r\n/g, '\n').trimEnd().split(/\n[ \t]*\n+/);
  let changed = false;
  for (const edit of updates) {
    const cues = blocks.slice(1).map((block, index) => readCueBlock(block, index + 1)).filter(Boolean);
    const identity = cues.find(cue => cue.id === edit.id);
    const beforeStart = edit.beforeStart ?? edit.start;
    const beforeEnd = edit.beforeEnd ?? edit.end;
    const exact = cues.filter(cue => near(cue.start, beforeStart) && near(cue.end, beforeEnd));
    const original = exact.find(cue => normalized(cue.text) === normalized(edit.before));
    if (!identity && !original && exact.length > 1) {
      throw new Error('Terdapat beberapa sari kata pada masa yang sama. Sari kata tidak diubah.');
    }
    const match = identity || original || exact[0];
    let affected = match ? [match] : [];
    if (!affected.length && edit.before) {
      const range = cues.filter(cue => !cue.id.startsWith('studio-')
        && cue.start >= beforeStart - 0.002 && cue.end <= beforeEnd + 0.002);
      if (range.length > 1 && near(range[0].start, beforeStart) && near(range.at(-1).end, beforeEnd)) {
        affected = range;
      }
    }
    const newTiming = `${formatVttTime(edit.start)} --> ${formatVttTime(edit.end)}`;
    const timing = match?.timing && !edit.retimed ? match.timing
      : newTiming + (match?.timing.match(/^\S+\s+-->\s+\S+(.*)$/)?.[1] || '');
    const replacement = edit.line ? `${edit.id}\n${timing}\n${escapeCueText(edit.line)}` : '';
    const originalIndex = affected[0]?.index;
    if (affected.length === 1 && blocks[originalIndex] === replacement) continue;
    if (!affected.length && !replacement) continue;
    for (const cue of affected.toReversed()) blocks.splice(cue.index, 1);
    if (replacement) {
      const retimedStart = edit.retimed && affected.length && affected[0].start !== edit.start;
      const index = originalIndex === undefined || retimedStart
        ? blocks.findIndex((block, i) => i > 0 && cueBlockStart(block) > edit.start) : originalIndex;
      blocks.splice(index < 0 ? blocks.length : index, 0, replacement);
    }
    changed = true;
  }
  return changed ? blocks.join('\n\n') + '\n' : withAdditions;
}
