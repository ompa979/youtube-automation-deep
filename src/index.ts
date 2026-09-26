/**
 * Cloudflare Worker entry point.
 *
 * Two handlers:
 *   - scheduled(): Cron Trigger fires every hour → pushes a job onto the Queue.
 *   - queue():     Queue consumer → runs the container, uploads to R2/YouTube,
 *                  and reports the result back to the Durable Object.
 */
import type { Env, ShortsJob, JobResult } from "./types";
import { runPipelineJob } from "./container";
import { decodeCreds, uploadToYouTube } from "./youtube";

// ─── Cron Handler ─────────────────────────────────────────────────────────────

async function handleScheduled(
  _controller: ScheduledController,
  env: Env,
  _ctx: ExecutionContext,
): Promise<void> {
  console.log("[cron] Hourly trigger fired");

  // Load the content plan. In production this could live in R2 or KV;
  // for now it's baked into the Worker bundle as a static import.
  // (See `content_plan.json` in the repo root — Wrangler will inline it.)
  // For simplicity we fetch it from R2 on first use.
  const planObj = await env.VIDEO_BUCKET.get("content_plan.json");
  if (!planObj) {
    console.error("[cron] content_plan.json not found in R2. Upload it first.");
    return;
  }
  const contentPlan = await planObj.json<Record<string, { topics: string[] }>>();

  const niches = env.NICHES_ENABLED.split(",").map((s) => s.trim()).filter(Boolean);
  const languages = env.LANGUAGES_ENABLED.split(",").map((s) => s.trim()).filter(Boolean);

  const stateId = env.PIPELINE_STATE.idFromName("global");
  const state = env.PIPELINE_STATE.get(stateId) as DurableObjectStub;

  const job = await state.pickNextJob(niches, languages, contentPlan) as ShortsJob | null;

  if (!job) {
    console.warn("[cron] All niches exhausted. Add more topics to content_plan.json.");
    return;
  }

  console.log(`[cron] Queuing job: niche=${job.niche} topic="${job.topic}" lang=${job.language}`);
  await env.SHORTS_QUEUE.send(job);
}

// ─── Queue Consumer ───────────────────────────────────────────────────────────

async function handleQueue(
  batch: MessageBatch<ShortsJob>,
  env: Env,
  _ctx: ExecutionContext,
): Promise<void> {
  for (const message of batch.messages) {
    const job = message.body;
    console.log(`[queue] Processing: niche=${job.niche} topic="${job.topic}"`);

    const stateId = env.PIPELINE_STATE.idFromName("global");
    const state = env.PIPELINE_STATE.get(stateId) as DurableObjectStub;

    const jobId = await state.logJobStart(job) as number;

    // ── 1. Pick a YouTube project with quota left ──────────────────────────
    const projectNames: string[] = [];
    for (let i = 1; i <= 6; i++) {
      const raw = (env as unknown as Record<string, string | undefined>)[`YT_CREDS_${i}`];
      if (raw) projectNames.push(`yt_project_${i}`);
    }

    const chosenProject = await state.pickYouTubeProject(projectNames) as string | null;

    if (!chosenProject) {
      const err = "All YouTube projects exhausted their daily quota.";
      console.error(`[queue] ${err}`);
      await state.logJobComplete(jobId, { success: false, error: err });
      message.ack();
      continue;
    }

    // ── 2. Gather secrets for the container ────────────────────────────────
    const secrets: Record<string, string> = {};
    if (env.GEMINI_API_KEY)      secrets.GEMINI_API_KEY = env.GEMINI_API_KEY;
    if (env.OPENROUTER_API_KEY)  secrets.OPENROUTER_API_KEY = env.OPENROUTER_API_KEY;
    if (env.PEXELS_API_KEY)      secrets.PEXELS_API_KEY = env.PEXELS_API_KEY;
    if (env.PIXABAY_API_KEY)     secrets.PIXABAY_API_KEY = env.PIXABAY_API_KEY;
    secrets.TTS_PROVIDER = "edge";
    secrets.TTS_LANGUAGE = "hi-IN";
    secrets.TTS_VOICE = "hi-IN-MadhurNeural,hi-IN-SwaraNeural";
    secrets.TTS_NO_FALLBACK = "false";

    // ── 3. Run the container ───────────────────────────────────────────────
    const result = await runPipelineJob(env, job, secrets);

    if (!result.success || !result.videoBase64) {
      const err = result.error ?? "Container returned no video.";
      console.error(`[queue] Container failed: ${err}`);
      await state.logJobComplete(jobId, { success: false, error: err });
      message.retry({ delaySeconds: 60 });
      continue;
    }

    // ── 4. Store the video in R2 ───────────────────────────────────────────
    const videoBytes = Uint8Array.from(atob(result.videoBase64), (c) => c.charCodeAt(0));
    const slug = job.topic.toLowerCase().replace(/[^\w]+/g, "-").slice(0, 60);
    const videoKey = `videos/${Date.now()}-${slug}.mp4`;

    await env.VIDEO_BUCKET.put(videoKey, videoBytes, {
      httpMetadata: { contentType: "video/mp4" },
    });
    console.log(`[queue] Video stored in R2: ${videoKey}`);

    // ── 5. Upload to YouTube (if enabled) ──────────────────────────────────
    let uploadResult: JobResult = { success: true, videoKey };

    if (env.UPLOAD_ENABLED === "1") {
      const credRaw = (env as unknown as Record<string, string | undefined>)[
        `YT_CREDS_${chosenProject.split("_").pop()}`
      ];
      const creds = credRaw ? decodeCreds(credRaw) : null;

      if (!creds) {
        const err = `Could not decode credentials for ${chosenProject}`;
        console.error(`[queue] ${err}`);
        await state.logJobComplete(jobId, { success: false, videoKey, error: err });
        message.ack();
        continue;
      }

      const script = (result.script ?? {}) as {
        title: string;
        description: string;
        tags: string[];
      };

      try {
        const up = await uploadToYouTube(
          videoBytes.buffer,
          {
            title: script.title ?? job.topic,
            description: script.description ?? job.topic,
            tags: script.tags ?? [],
          },
          creds,
          chosenProject,
        );

        await state.recordUpload(chosenProject);
        uploadResult = { success: true, videoKey, videoId: up.videoId, project: chosenProject };
        console.log(`[queue] Uploaded → ${up.url}  (project=${chosenProject})`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`[queue] Upload failed: ${msg}`);
        await state.logJobComplete(jobId, { success: false, videoKey, error: msg });
        message.retry({ delaySeconds: 120 });
        continue;
      }
    } else {
      console.log("[queue] UPLOAD_ENABLED=0 — skipping upload (dry run).");
    }

    // ── 6. Persist job result ──────────────────────────────────────────────
    await state.logJobComplete(jobId, uploadResult);
    message.ack();
  }
}

// ─── Export ───────────────────────────────────────────────────────────────────

export { PipelineState } from "./durable-object";
export { VideoPipelineContainer } from "./container";

export default {
  async scheduled(
    controller: ScheduledController,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<void> {
    return handleScheduled(controller, env, ctx);
  },

  async queue(
    batch: MessageBatch<ShortsJob>,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<void> {
    return handleQueue(batch, env, ctx);
  },

  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Health check / stats endpoint.
    if (url.pathname === "/stats") {
      const stateId = env.PIPELINE_STATE.idFromName("global");
      const state = env.PIPELINE_STATE.get(stateId) as DurableObjectStub;
      const stats = await state.getStats();
      return Response.json(stats);
    }

    if (url.pathname === "/health") {
      return Response.json({ ok: true, ts: new Date().toISOString() });
    }

    // Manual trigger for testing: POST /trigger
    if (request.method === "POST" && url.pathname === "/trigger") {
      const job: ShortsJob = {
        niche: "exam_concepts",
        topic: "why the sky is blue and what Rayleigh scattering actually means",
        language: "hinglish",
        requestedAt: new Date().toISOString(),
      };
      await env.SHORTS_QUEUE.send(job);
      return Response.json({ queued: true, job });
    }

    return new Response(
      "cf-youtube-pipeline Worker\n\nEndpoints:\n  GET  /health\n  GET  /stats\n  POST /trigger\n",
      { status: 200 },
    );
  },
} satisfies ExportedHandler<Env>;
