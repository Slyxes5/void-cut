/**
 * Utility functions for managing YouTube cookies in client-side localStorage.
 * Ensures zero persistent storage of sensitive user cookies on the server.
 */

export const COOKIES_STORAGE_KEY = 'cheatclip_youtube_cookies';
const LEGACY_STORAGE_KEY = 'youtube_cookies';

// Core YouTube and Google domains relevant for video authentication and HD streams
const RELEVANT_DOMAINS = ['youtube.com', 'google.com', 'googlevideo.com', 'youtu.be', 'gvt1.com'];

/**
 * Checks if a line contains a valid Netscape cookie record.
 */
function isValidCookieRecord(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed) return false;
  // Skip pure comments and headers
  if (trimmed.startsWith('#') && !trimmed.startsWith('#HttpOnly_')) return false;

  const effective = trimmed.startsWith('#HttpOnly_') ? trimmed.slice(10).trim() : trimmed;
  const parts = effective.split('\t');
  // Netscape cookies have 7 tab-separated columns:
  // domain, flag, path, secure, expiration, name, value
  if (parts.length >= 6 && parts[0] && parts[5]) {
    const domain = parts[0].trim().toLowerCase();
    const name = parts[5].trim();
    return Boolean(domain && name);
  }
  return false;
}

/**
 * Checks if the content string contains at least one valid, active cookie line.
 */
export function hasValidCookieLines(content: string): boolean {
  if (!content || typeof content !== 'string') return false;
  const lines = content.split('\n');
  for (const line of lines) {
    if (isValidCookieRecord(line)) {
      return true;
    }
  }
  return false;
}

/**
 * Counts the number of active, valid cookie lines in a cookie string.
 */
export function countValidCookies(content: string): number {
  if (!content || typeof content !== 'string') return 0;
  const lines = content.split('\n');
  let count = 0;
  for (const line of lines) {
    if (isValidCookieRecord(line)) {
      count++;
    }
  }
  return count;
}

/**
 * Normalizes raw cookie string (Netscape, JSON, HTTP Header, or space-separated)
 * into standard, 100% valid Netscape format for yt-dlp.
 */
export function normalizeCookies(rawContent: string): string {
  if (!rawContent) return '';

  // 1. Clean BOM, null bytes, and normalize line endings
  const text = rawContent
    .replace(/\ufeff/g, '')
    .replace(/\ufffe/g, '')
    .replace(/\x00/g, '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .trim();

  if (!text) return '';

  // 2. Check for JSON format (e.g. from Cookie-Editor, EditThisCookie, Playwright)
  if (text.startsWith('[') || text.startsWith('{')) {
    try {
      let parsed = JSON.parse(text);
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
        if (Array.isArray(parsed.cookies)) {
          parsed = parsed.cookies;
        } else if (Array.isArray(parsed.data)) {
          parsed = parsed.data;
        } else {
          parsed = [parsed];
        }
      }

      if (Array.isArray(parsed) && parsed.length > 0 && typeof parsed[0] === 'object') {
        const lines: string[] = [
          '# Netscape HTTP Cookie File',
          '# http://curl.haxx.se/rfc/cookie_spec.html',
          '# Converted automatically from JSON format by Void Cut',
          '',
        ];
        let count = 0;
        for (const item of parsed) {
          if (!item || typeof item !== 'object') continue;
          let domain = String(item.domain || item.host || '').trim();
          if (!domain) domain = '.youtube.com';

          const flag = domain.startsWith('.') ? 'TRUE' : 'FALSE';
          const path = String(item.path || '/').trim() || '/';
          const secure = item.secure ? 'TRUE' : 'FALSE';
          let expInt = Math.floor(Number(item.expirationDate || item.expires || item.expiry || 0));
          if (isNaN(expInt) || expInt <= 0) {
            expInt = 2147483647; // 2038 fallback for session cookies
          }
          const name = String(item.name || '').trim();
          const val = String(item.value || '').trim();
          const isHttpOnly = Boolean(item.httpOnly);

          if (name) {
            const prefix = isHttpOnly ? '#HttpOnly_' : '';
            lines.push(`${prefix}${domain}\t${flag}\t${path}\t${secure}\t${expInt}\t${name}\t${val}`);
            count++;
          }
        }
        if (count > 0) {
          return lines.join('\n') + '\n';
        }
      }
    } catch {
      // Fallback to text parsing
    }
  }

  // 3. Check for HTTP Cookie header format (e.g. from DevTools Network tab or curl: "Cookie: SID=...; HSID=...")
  let cleanHeader = text;
  if (cleanHeader.toLowerCase().startsWith('cookie:')) {
    cleanHeader = cleanHeader.slice(7).trim();
  }
  if (cleanHeader.includes(';') && cleanHeader.includes('=') && !cleanHeader.includes('\t') && !cleanHeader.includes('# Netscape')) {
    const pairs = cleanHeader.split(';');
    const headerLines: string[] = [
      '# Netscape HTTP Cookie File',
      '# http://curl.haxx.se/rfc/cookie_spec.html',
      '# Converted automatically from HTTP Cookie header by Void Cut',
      '',
    ];
    let cookieCount = 0;
    for (const pair of pairs) {
      const trimmedPair = pair.trim();
      if (!trimmedPair || !trimmedPair.includes('=')) continue;
      const eqIdx = trimmedPair.indexOf('=');
      const name = trimmedPair.slice(0, eqIdx).trim();
      const val = trimmedPair.slice(eqIdx + 1).trim();
      if (name) {
        headerLines.push(`.youtube.com\tTRUE\t/\tTRUE\t2147483647\t${name}\t${val}`);
        cookieCount++;
      }
    }
    if (cookieCount > 0) {
      return headerLines.join('\n') + '\n';
    }
  }

  // 4. Handle standard Netscape text and space-to-tab repair
  const rawLines = text.split('\n');
  const cleanedLines: string[] = [];
  let hasHeader = false;

  for (const line of rawLines) {
    let stripped = line.trim();
    if (!stripped) continue;

    if (stripped.includes('# Netscape HTTP Cookie File')) {
      hasHeader = true;
      cleanedLines.push(stripped);
      continue;
    }

    if (stripped.startsWith('#') && !stripped.startsWith('#HttpOnly_')) {
      cleanedLines.push(stripped);
      continue;
    }

    // If spaces were pasted instead of tabs, repair 2+ spaces to tabs
    if (!stripped.includes('\t')) {
      const parts = stripped.split(/\s{2,}|\t/);
      if (parts.length >= 6) {
        stripped = parts.join('\t');
      }
    }

    // Check for DevTools Application Cookies table copy (Name\tValue\tDomain\tPath...)
    const tabParts = stripped.split('\t');
    if (tabParts.length >= 4) {
      // If column 2 looks like a domain (.youtube.com or google.com) and column 0 looks like a cookie name
      const col2 = tabParts[2]?.trim().toLowerCase();
      if (col2 && (col2.startsWith('.') || col2.includes('youtube') || col2.includes('google'))) {
        const name = tabParts[0].trim();
        const val = tabParts[1].trim();
        const domain = tabParts[2].trim();
        const path = tabParts[3].trim() || '/';
        const expRaw = tabParts[4] ? Date.parse(tabParts[4]) : 0;
        const expInt = !isNaN(expRaw) && expRaw > 0 ? Math.floor(expRaw / 1000) : 2147483647;
        const flag = domain.startsWith('.') ? 'TRUE' : 'FALSE';
        stripped = `${domain}\t${flag}\t${path}\tTRUE\t${expInt}\t${name}\t${val}`;
      }
    }

    cleanedLines.push(stripped);
  }

  // 5. Size optimization guard: if the file is massive (> 100KB), keep only YouTube & Google cookies
  // to prevent blowing browser localStorage quota (typically 5MB limit).
  let finalLines = cleanedLines;
  if (finalLines.length > 300) {
    const essential = finalLines.filter((l) => {
      const lower = l.toLowerCase();
      if (lower.startsWith('#') && !lower.startsWith('#httponly_')) return true;
      return RELEVANT_DOMAINS.some((d) => lower.includes(d));
    });
    if (essential.length > 5) {
      finalLines = essential;
    }
  }

  if (!hasHeader) {
    finalLines.unshift(
      '# Netscape HTTP Cookie File',
      '# http://curl.haxx.se/rfc/cookie_spec.html',
      ''
    );
  }

  return finalLines.join('\n') + '\n';
}

/**
 * Extracts unique sample domain names from cookie text for preview.
 */
export function getCookieDomainSamples(content: string, maxSamples = 6): string[] {
  if (!content) return [];
  const sampleDomains: string[] = [];
  const lines = content.split('\n');

  for (const line of lines) {
    let trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.startsWith('#') && !trimmed.startsWith('#HttpOnly_')) continue;
    if (trimmed.startsWith('#HttpOnly_')) {
      trimmed = trimmed.slice(10).trim();
    }

    const parts = trimmed.split('\t');
    if (parts.length >= 1 && parts[0]) {
      const domain = parts[0].trim();
      if (domain && !sampleDomains.includes(domain)) {
        sampleDomains.push(domain);
        if (sampleDomains.length >= maxSamples) break;
      }
    }
  }

  return sampleDomains;
}

/**
 * Retrieves YouTube cookies from browser localStorage with legacy migration.
 */
export function getStoredCookies(): string {
  try {
    let stored = localStorage.getItem(COOKIES_STORAGE_KEY) || '';
    if (!stored) {
      const legacy = localStorage.getItem(LEGACY_STORAGE_KEY) || '';
      if (legacy) {
        stored = legacy;
        // Migrate to primary key and clean legacy to reclaim storage quota
        try {
          localStorage.setItem(COOKIES_STORAGE_KEY, legacy);
          localStorage.removeItem(LEGACY_STORAGE_KEY);
        } catch {
          // ignore migration error
        }
      }
    }
    return stored.trim();
  } catch {
    return '';
  }
}

/**
 * Saves YouTube cookies to browser localStorage.
 * Automatically prunes non-essential domains if localStorage quota is tight.
 * Dispatches synchronization events so all components update immediately.
 */
export function setStoredCookies(content: string): boolean {
  if (!content) {
    removeStoredCookies();
    return true;
  }

  const normalized = normalizeCookies(content);
  if (!normalized || !hasValidCookieLines(normalized)) {
    throw new Error('No valid YouTube cookies found. Please paste valid cookies in Netscape .txt, JSON, or Header format.');
  }

  try {
    localStorage.setItem(COOKIES_STORAGE_KEY, normalized);
    // Remove legacy key to prevent storing duplicate multi-megabyte payloads
    localStorage.removeItem(LEGACY_STORAGE_KEY);

    // Notify all active React components and windows
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('cheatclip_cookies_updated', { detail: { hasCookies: true } }));
      window.dispatchEvent(new StorageEvent('storage', { key: COOKIES_STORAGE_KEY, newValue: normalized }));
    }
    return true;
  } catch (err: any) {
    // If browser quota exceeded, aggressively prune strictly to essential YouTube auth cookies
    console.warn('[cookieUtils] Primary localStorage write failed, attempting aggressive pruning:', err?.message);
    try {
      const lines = normalized.split('\n');
      const criticalKeys = ['login_info', 'sid', 'hsid', 'ssid', 'apisid', 'sapisid', '__secure', 'pref', 'visitor_info1_live', 'ysc'];
      const pruned = lines.filter((line) => {
        const lower = line.toLowerCase();
        if (lower.startsWith('#') && !lower.startsWith('#httponly_')) return true;
        if (!lower.includes('youtube.com') && !lower.includes('google.com')) return false;
        return criticalKeys.some((k) => lower.includes(k));
      });
      const prunedStr = pruned.join('\n') + '\n';
      localStorage.setItem(COOKIES_STORAGE_KEY, prunedStr);
      localStorage.removeItem(LEGACY_STORAGE_KEY);

      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('cheatclip_cookies_updated', { detail: { hasCookies: true } }));
        window.dispatchEvent(new StorageEvent('storage', { key: COOKIES_STORAGE_KEY, newValue: prunedStr }));
      }
      return true;
    } catch (finalErr: any) {
      console.error('[cookieUtils] Failed to write cookies to localStorage:', finalErr);
      throw new Error(`LocalStorage is full or restricted: ${finalErr?.message || 'QuotaExceededError'}. Please clear browser cache.`);
    }
  }
}

/**
 * Removes YouTube cookies from browser localStorage.
 */
export function removeStoredCookies(): void {
  try {
    localStorage.removeItem(COOKIES_STORAGE_KEY);
    localStorage.removeItem(LEGACY_STORAGE_KEY);
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('cheatclip_cookies_updated', { detail: { hasCookies: false } }));
      window.dispatchEvent(new StorageEvent('storage', { key: COOKIES_STORAGE_KEY, newValue: null }));
    }
  } catch (e) {
    console.error('Failed to remove cookies from localStorage:', e);
  }
}

/**
 * Checks if user has valid stored YouTube cookies in localStorage.
 * Strictly verifies that actual cookie records exist (not just comments/headers).
 */
export function hasStoredCookies(): boolean {
  const c = getStoredCookies();
  return hasValidCookieLines(c);
}

/**
 * Returns exact count of active cookies currently stored.
 */
export function getStoredCookiesCount(): number {
  const c = getStoredCookies();
  return countValidCookies(c);
}

/**
 * Returns size in bytes of stored cookies.
 */
export function getStoredCookiesSize(): number {
  const c = getStoredCookies();
  return c ? new Blob([c]).size : 0;
}
