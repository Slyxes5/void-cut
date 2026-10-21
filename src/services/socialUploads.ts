export type SocialPlatform = 'youtube' | 'tiktok';
export interface SocialAccount {
  platform: SocialPlatform;
  configured: boolean;
  connected: boolean;
  account_name: string;
  account_id: string;
  error: string;
  direct_enabled: boolean;
  direct_audited: boolean;
}
export interface CreatorInfo {
  creator_nickname: string;
  privacy_level_options: string[];
  comment_disabled: boolean;
  duet_disabled: boolean;
  stitch_disabled: boolean;
  max_video_post_duration_sec: number;
}
export interface SocialTarget {
  platform: SocialPlatform;
  account_id: string;
  mode: 'upload' | 'draft' | 'direct';
  privacy: string;
  made_for_kids?: boolean;
  contains_synthetic_media?: boolean;
  allow_comment?: boolean;
  allow_duet?: boolean;
  allow_stitch?: boolean;
  commercial?: boolean;
  own_brand?: boolean;
  branded_content?: boolean;
  music_consent?: boolean;
}
export interface SocialJob {
  request_id: string;
  batch_id: string;
  clip_index: number;
  title: string;
  caption: string;
  created_at: number;
  targets: Array<SocialTarget & {
    status: 'queued' | 'uploading' | 'processing' | 'completed' | 'draft' | 'failed' | 'uncertain';
    progress: number;
    message: string;
    url?: string;
  }>;
}
export async function socialApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/social/${path}`, {
    credentials: 'same-origin', cache: 'no-store', ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error('Respons upload tidak terbaca. Periksa status sebelum mencoba lagi.'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Permintaan upload ditolak. Periksa kolom dan pengaturan akun.');
  return data as T;
}
export function uploadReceiptUrl(url: string | null | undefined): string | undefined {
  if (!url) return;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'https:' && !parsed.username && !parsed.password && !parsed.port &&
        ((parsed.hostname === 'www.youtube.com' && /^\/shorts\/[\w-]{11}$/.test(parsed.pathname)) ||
         (parsed.hostname === 'www.tiktok.com' && /^\/@[^/]+\/video\/\d+$/.test(parsed.pathname)))) return url;
  } catch { /* A receipt link is optional. */ }
}
