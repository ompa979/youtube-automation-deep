/**
 * YouTube Data API v3 upload helper.
 *
 * Handles OAuth token refresh and resumable upload. The rotating project
 * credential is passed in as a decoded JSON blob.
 */
import type { ShortsJob } from "./types";

interface YouTubeCreds {
  client_id: string;
  client_secret: string;
  refresh_token: string;
  token_uri?: string;
}

interface UploadResult {
  videoId: string;
  url: string;
  project: string;
}

/**
 * Decode the base64-encoded YT_CREDS_N secret into a credential object.
 * Accepts either base64-encoded JSON or raw JSON (for local dev).
 */
export function decodeCreds(raw: string): YouTubeCreds | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;

  // Try base64 first.
  try {
    const decoded = atob(trimmed);
    return JSON.parse(decoded) as YouTubeCreds;
  } catch {
    // Fall through to raw JSON.
  }

  try {
    return JSON.parse(trimmed) as YouTubeCreds;
  } catch {
    return null;
  }
}

/**
 * Exchange a refresh token for a short-lived access token.
 */
async function getAccessToken(creds: YouTubeCreds): Promise<string> {
  const tokenUri = creds.token_uri ?? "https://oauth2.googleapis.com/token";

  const body = new URLSearchParams({
    client_id: creds.client_id,
    client_secret: creds.client_secret,
    refresh_token: creds.refresh_token,
    grant_type: "refresh_token",
  });

  const res = await fetch(tokenUri, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Token refresh failed (${res.status}): ${text.slice(0, 300)}`);
  }

  const json = await res.json<{ access_token: string }>();
  return json.access_token;
}

/**
 * Upload a video to YouTube. The video bytes are read from R2.
 *
 * This uses a single-request upload (not resumable) because Shorts are
 * small (<100 MB). For larger files, switch to the resumable endpoint.
 */
export async function uploadToYouTube(
  videoBytes: ArrayBuffer,
  script: { title: string; description: string; tags: string[] },
  creds: YouTubeCreds,
  projectName: string,
  categoryId = "27", // 27 = Education
): Promise<UploadResult> {
  const accessToken = await getAccessToken(creds);

  // Build the multipart/related body required by the YouTube API.
  const boundary = "bds_" + crypto.randomUUID().replace(/-/g, "");

  const metadata = JSON.stringify({
    snippet: {
      title: script.title.slice(0, 100),
      description: script.description.slice(0, 4900),
      tags: script.tags.slice(0, 15),
      categoryId,
    },
    status: {
      privacyStatus: "public",
      selfDeclaredMadeForKids: false,
    },
  });

  const encoder = new TextEncoder();
  const parts: Uint8Array[] = [];

  parts.push(encoder.encode(
    `--${boundary}\r\n` +
    `Content-Type: application/json; charset=UTF-8\r\n\r\n` +
    `${metadata}\r\n`,
  ));

  parts.push(encoder.encode(
    `--${boundary}\r\n` +
    `Content-Type: video/mp4\r\n\r\n`,
  ));

  parts.push(new Uint8Array(videoBytes));

  parts.push(encoder.encode(`\r\n--${boundary}--`));

  // Concatenate all parts into a single Uint8Array.
  const totalLength = parts.reduce((sum, p) => sum + p.byteLength, 0);
  const body = new Uint8Array(totalLength);
  let offset = 0;
  for (const part of parts) {
    body.set(part, offset);
    offset += part.byteLength;
  }

  const res = await fetch(
    "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=multipart&part=snippet,status",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": `multipart/related; boundary=${boundary}`,
        "Content-Length": String(totalLength),
      },
      body: body.buffer,
    },
  );

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`YouTube upload failed (${res.status}): ${text.slice(0, 500)}`);
  }

  const json = await res.json<{ id: string }>();
  return {
    videoId: json.id,
    url: `https://youtu.be/${json.id}`,
    project: projectName,
  };
}
