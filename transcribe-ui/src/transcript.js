const speakerLine = /^([^:\r\n]{1,80}):(?:[ \t]+|$)(.*)$/s;

export function splitSpeaker(line) {
  const match = speakerLine.exec(line);
  return match ? { speaker: match[1].trim(), text: match[2] } : { speaker: '', text: line };
}

export function transcriptRows(text) {
  const rows = text.split(/\r?\n/).filter(line => line.trim()).map((line, index) => ({
    id: index + 1,
    ...splitSpeaker(line),
  }));
  return rows.length ? rows : [{ id: 1, speaker: '', text: '' }];
}

function speechKey(text) {
  return text.replace(/\s+/g, ' ').trim();
}

export function assignRowTimes(rows, segments, savedTimings) {
  if (Array.isArray(savedTimings) && rows.length === savedTimings.length
      && rows.every((row, index) => transcriptText([row]) === savedTimings[index]?.line)) {
    rows.forEach((row, index) => {
      const { start, end, cueId } = savedTimings[index];
      if (Number.isFinite(start)) Object.assign(row, { start, end, cueId });
    });
    return;
  }
  if (!Array.isArray(segments)) return;

  const cues = segments.flatMap(segment => {
    const start = segment.startSeconds ?? segment.start;
    const end = segment.endSeconds ?? segment.end;
    return Number.isFinite(start) && typeof segment.text === 'string'
      ? [{ start, end, text: speechKey(segment.text), used: false }] : [];
  }).sort((a, b) => a.start - b.start);
  const byText = new Map();
  for (const cue of cues) {
    if (!byText.has(cue.text)) byText.set(cue.text, []);
    byText.get(cue.text).push(cue);
  }

  for (const row of rows) {
    const match = byText.get(speechKey(row.text))?.shift();
    if (match) {
      match.used = true;
      Object.assign(row, { start: match.start, end: match.end });
    }
  }

  for (const row of rows.filter(row => !Number.isFinite(row.start) && row.text.trim())) {
    const text = speechKey(row.text);
    for (let first = 0; first < cues.length; first++) {
      if (cues[first].used || !cues[first].text || !text.startsWith(cues[first].text + ' ')) continue;
      let combined = cues[first].text;
      for (let last = first + 1; last < cues.length && !cues[last].used; last++) {
        combined += ' ' + cues[last].text;
        if (combined === text) {
          Object.assign(row, { start: cues[first].start, end: cues[last].end });
          cues.slice(first, last + 1).forEach(cue => { cue.used = true; });
          break;
        }
        if (!text.startsWith(combined + ' ')) break;
      }
      if (Number.isFinite(row.start)) break;
    }
  }
}

export function sortTranscriptRows(rows) {
  const order = row => Number.isFinite(row.start) ? row.start : Infinity;
  rows.sort((left, right) => order(left) - order(right));
}

export function rowTimings(rows) {
  return rows.flatMap(row => {
    const line = transcriptText([row]);
    return line ? [{ line, start: row.start ?? null, end: row.end ?? null, cueId: row.cueId }] : [];
  });
}

export function cleanSpeaker(value) {
  return value.replace(/[:\r\n]+/g, ' ').trim().slice(0, 80);
}

export function transcriptText(rows) {
  return rows.map(row => {
    const speaker = cleanSpeaker(row.speaker);
    const text = row.text.replace(/\s*\r?\n\s*/g, ' ').trim();
    return text && (speaker ? `${speaker}: ${text}` : text);
  }).filter(Boolean).join('\n');
}

export function renameSpeaker(rows, oldName, newName) {
  const name = cleanSpeaker(newName);
  if (!oldName || !name || oldName === name) return;
  for (const row of rows) {
    if (cleanSpeaker(row.speaker) === oldName) row.speaker = name;
  }
}
