"""Caption timing without minimum-duration padding or cumulative timestamp shifts."""
import math
import re


def normalize_words(words, duration=None):
    """Keep acoustic starts, clip to media bounds and trim overlaps, never delay words."""
    result = []
    for item in words:
        try:
            start, end = float(item['start']), float(item['end'])
            text = str(item.get('word', '')).strip()
        except (TypeError, ValueError, KeyError):
            continue
        if not text or not math.isfinite(start) or not math.isfinite(end):
            continue
        start = max(0.0, start)
        if duration is not None:
            end = min(end, duration)
        if end > start:
            result.append({**item, 'word': text, 'start': start, 'end': end})
    result.sort(key=lambda w: w['start'])
    for i in range(len(result) - 1):
        result[i]['end'] = min(result[i]['end'], result[i + 1]['start'])
    return [w for w in result if w['end'] > w['start']]


def caption_chunks(words):
    """Mirrored in src/utils/subtitleTiming.ts for preview/export parity."""
    chunks, current, chars = [], [], 0
    for word in words:
        size = len(word['word'])
        previous = current[-1] if current else None
        if previous and (len(current) >= 3 or word['start'] - previous['end'] >= .38
                         or re.search(r'[.!?]$', previous['word'])
                         or chars + size > 22):
            chunks.append(current)
            current, chars = [], 0
        current.append(word)
        chars += size + 1
    if current:
        chunks.append(current)
    return chunks


def transcript_words(transcript, clip_start, clip_end):
    """Crop AFTER distributing a full line, so cuts never squeeze old words into the clip.

    Existing per-word timestamps take precedence. Line-only tracks remain estimates.
    """
    words = []
    for line in transcript:
        if isinstance(line.get('words'), list) and line['words']:
            candidates = line['words']
        else:
            parts = str(line.get('text', '')).split()
            start = float(line.get('start', 0))
            end = float(line.get('end', start + float(line.get('duration', 0))))
            if end <= start:
                end = start + max(1.8, len(parts) * .38)
            candidates = [dict(word=word, start=start + (end-start)*i/len(parts),
                               end=start + (end-start)*(i+1)/len(parts))
                          for i, word in enumerate(parts)]
        for word in candidates:
            words.append({**word, 'start': float(word['start']) - clip_start,
                          'end': float(word['end']) - clip_start})
    duration = clip_end - clip_start if clip_end > clip_start else None
    return normalize_words(words, duration)
