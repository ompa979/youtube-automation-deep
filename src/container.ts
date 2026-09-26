/**
 * VideoPipelineContainer — Cloudflare Container wrapper.
 *
 * This class extends the `Container` base class from @cloudflare/containers.
 * The container itself runs a Python HTTP server (container/server.py) that
 * exposes a /generate endpoint. This Worker-side class is responsible for
 * starting the container and routing a job payload to it.
 */
import { Container, getContainer } from "@cloudflare/containers";
import type { Env, ShortsJob } from "./types";

export class VideoPipelineContainer extends Container {
  defaultPort = 8080;
  sleepAfter = "10m"; // shut down after 10 minutes of inactivity

  override onStart(): void {
    console.log("[container] VideoPipelineContainer started");
  }

  override onStop(): void {
    console.log("[container] VideoPipelineContainer stopped");
  }

  override onError(error: unknown): void {
    console.error("[container] VideoPipelineContainer error:", error);
  }
}

/**
 * Run one generation job inside the container.
 *
 * The container expects a JSON POST body:
 *   {
 *     "niche": "facts",
 *     "topic": "octopus has three hearts",
 *     "language": "en",
 *     "geminiApiKey": "...",
 *     "pexelsApiKey": "...",
 *     ...
 *   }
 *
 * It responds with:
 *   { "success": true, "videoBase64": "...", "script": {...} }
 *
 * The Worker then uploads the video to R2 and (optionally) to YouTube.
 */
export async function runPipelineJob(
  env: Env,
  job: ShortsJob,
  secrets: Record<string, string>,
): Promise<{
  success: boolean;
  videoBase64?: string;
  script?: unknown;
  error?: string;
}> {
  const container = getContainer(env.PIPELINE_CONTAINER);

  const payload = {
    niche: job.niche,
    topic: job.topic,
    language: job.language,
    ...secrets,
  };

  try {
    await container.start({
      envVars: {
        JOB_PAYLOAD: JSON.stringify(payload),
      },
    });

    const response = await container.fetch(
      new Request("http://container/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    );

    if (!response.ok) {
      const text = await response.text();
      return { success: false, error: `Container HTTP ${response.status}: ${text.slice(0, 500)}` };
    }

    const result = await response.json<{
      success: boolean;
      videoBase64?: string;
      script?: unknown;
      error?: string;
    }>();

    return result;
  } catch (err) {
    return {
      success: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
