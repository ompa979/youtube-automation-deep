/**
 * Shared type definitions for the Cloudflare pipeline.
 */

export interface Env {
  // Bindings
  PIPELINE_CONTAINER: DurableObjectNamespace;
  PIPELINE_STATE: DurableObjectNamespace;
  SHORTS_QUEUE: Queue<ShortsJob>;
  SHORTS_DLQ: Queue<ShortsJob>;
  VIDEO_BUCKET: R2Bucket;

  // Vars
  UPLOAD_ENABLED: string;
  NICHES_ENABLED: string;
  LANGUAGES_ENABLED: string;

  // Secrets (set via wrangler secret put)
  GEMINI_API_KEY?: string;
  OPENROUTER_API_KEY?: string;
  PEXELS_API_KEY?: string;
  PIXABAY_API_KEY?: string;
  TTS_PROVIDER?: string;
  TTS_LANGUAGE?: string;
  TTS_VOICE?: string;
  TTS_NO_FALLBACK?: string;
  YT_CREDS_1?: string;
  YT_CREDS_2?: string;
  YT_CREDS_3?: string;
  YT_CREDS_4?: string;
  YT_CREDS_5?: string;
  YT_CREDS_6?: string;
}

export interface ShortsJob {
  niche: string;
  topic: string;
  language: string;
  requestedAt: string;
}

export interface JobResult {
  success: boolean;
  videoKey?: string;      // R2 object key
  videoId?: string;       // YouTube video ID
  project?: string;       // YouTube project used
  error?: string;
}
