export function parseApiOrigin(value: string | undefined): string | null {
  if (!value?.trim()) return null;

  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    throw new Error(
      "NEXT_PUBLIC_API_BASE_URL must be an absolute HTTP(S) origin.",
    );
  }

  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error(
      "NEXT_PUBLIC_API_BASE_URL must be an HTTP(S) origin without credentials, a path, query, or fragment.",
    );
  }

  return url.origin;
}
