// Tiny fetch helper that unwraps the {ok, data, error} envelope.
// Server Components pass absolute URLs; `next` fetch cache is fine.

import { apiBase } from "./apiBase";

interface Envelope<T> {
  ok: boolean;
  data: T | null;
  error: { code?: string; message?: string } | null;
}

export async function fetchEnvelope<T>(
  path: string,
  init?: RequestInit & { revalidate?: number },
): Promise<T> {
  const url = path.startsWith("http") ? path : `${apiBase()}${path}`;
  const res = await fetch(url, {
    ...init,
    next: { revalidate: init?.revalidate ?? 60 },
  });
  if (!res.ok) throw new Error(`API ${res.status} ${url}`);
  const body = (await res.json()) as Envelope<T>;
  if (!body.ok || body.data == null) {
    throw new Error(body.error?.message ?? "API envelope error");
  }
  return body.data;
}

/**
 * Envelope fetcher that never throws — returns the data on success, or
 * ``fallback`` on any transport / envelope / parse failure. Use this in
 * Server Components that must render even if an optional backend endpoint
 * is missing or slow, so the route can hydrate instead of 500ing.
 */
export async function fetchEnvelopeSafe<T>(
  path: string,
  fallback: T,
  init?: RequestInit & { revalidate?: number },
): Promise<{ data: T; error: string | null }> {
  try {
    const data = await fetchEnvelope<T>(path, init);
    return { data, error: null };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { data: fallback, error: msg };
  }
}
