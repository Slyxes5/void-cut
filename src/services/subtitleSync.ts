import type { EditedSubtitleWord, ViralClip } from '../types';

export async function syncSubtitleAudio(videoUrl: string, clip: ViralClip, cookies?: string | null): Promise<EditedSubtitleWord[]> {
  const response = await fetch('/api/subtitles/sync', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video_url: videoUrl, start_time: clip.start_time, end_time: clip.end_time, cookies: cookies || undefined }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Sinkronisasi subtitle gagal. Coba lagi.');
  if (data.timing_source !== 'audio' || !Array.isArray(data.words) || data.words.some((w: EditedSubtitleWord) =>
    typeof w.word !== 'string' || !w.word.trim() || !Number.isFinite(w.start) || !Number.isFinite(w.end) ||
    w.start < clip.start_time || w.end > clip.end_time || w.end <= w.start)) {
    throw new Error('Timing subtitle dari server tidak valid.');
  }
  return data.words;
}
