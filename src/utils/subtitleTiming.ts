import type { EditedSubtitleWord, TranscriptLine, ViralClip } from '../types';

// The same boundary and grouping rules are used in backend/subtitle_timing.py.
export function normalizeSubtitleWords(words: EditedSubtitleWord[], clip: Pick<ViralClip, 'start_time' | 'end_time'>) {
  const ordered = words.flatMap(w => {
    const start = Math.max(clip.start_time, w.start);
    const end = Math.min(clip.end_time, w.end);
    return w.word.trim() && Number.isFinite(start) && Number.isFinite(end) && end > start
      ? [{ ...w, start, end }] : [];
  }).sort((a, b) => a.start - b.start);
  for (let i = 0; i < ordered.length - 1; i++) ordered[i].end = Math.min(ordered[i].end, ordered[i + 1].start);
  return ordered.filter(w => w.end > w.start);
}

export function wordsFromTranscript(clip: ViralClip, transcript: TranscriptLine[]): EditedSubtitleWord[] {
  const words = transcript.flatMap((row, group) => {
    const parts = row.text.trim().split(/\s+/).filter(Boolean);
    const source = row.words?.length ? row.words : parts.map((word, i) => ({
      word, start: row.start + (row.end - row.start) * i / parts.length,
      end: row.start + (row.end - row.start) * (i + 1) / parts.length,
    }));
    return source.map((w, i) => ({ ...w, id: `r${group}-w${i}`, group }));
  });
  return normalizeSubtitleWords(words, clip);
}

export function captionChunks(words: EditedSubtitleWord[]) {
  const chunks: EditedSubtitleWord[][] = [];
  let current: EditedSubtitleWord[] = [];
  let chars = 0;
  for (const word of words) {
    const size = [...word.word].length;
    const previous = current.at(-1);
    if (previous && (current.length >= 3 || word.start - previous.end >= .38 ||
        /[.!?]$/.test(previous.word) || chars + size > 22)) {
      chunks.push(current);
      current = [];
      chars = 0;
    }
    current.push(word);
    chars += size + 1;
  }
  if (current.length) chunks.push(current);
  return chunks;
}

export function buildCaptionFrames(words: EditedSubtitleWord[], clip: ViralClip, plain: boolean, lineLimit: number) {
  const ordered = normalizeSubtitleWords(words, clip);
  let groups: EditedSubtitleWord[][];
  if (plain) {
    groups = [];
    let current: EditedSubtitleWord[] = [];
    for (const word of ordered) {
      const prev = current.at(-1);
      if (prev && (word.start - prev.end > .45 || /[.!?]$/.test(prev.word) ||
          [...[...current, word].map(w => w.word).join(' ')].length > lineLimit * 2)) {
        groups.push(current);
        current = [];
      }
      current.push(word);
    }
    if (current.length) groups.push(current);
  } else groups = captionChunks(ordered);

  // libass uses centiseconds relative to the sliced clip. Match its rounding,
  // including clips whose absolute start falls between centiseconds.
  const relative = (time: number) => Math.floor((time - clip.start_time) * 100 + .5) / 100;
  return groups.flatMap(group => plain ? [{
    start: relative(group[0].start), end: relative(group[group.length - 1].end), words: group, activeId: '',
  }] : group.map(word => ({
    start: relative(word.start), end: relative(word.end), words: group, activeId: word.id,
  })));
}
