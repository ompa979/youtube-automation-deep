/**
 * PipelineState — Durable Object for job coordination.
 *
 * Responsibilities:
 *   - Topic rotation (round-robin across niches and languages)
 *   - YouTube quota tracking per rotating project
 *   - Job status log
 *
 * Uses SQLite-backed storage for durability across Worker restarts.
 */
import { DurableObject } from "cloudflare:workers";
import type { Env, ShortsJob } from "./types";

interface QuotaRow {
  project: string;
  units: number;
  day: string;
}

interface RotationRow {
  last_niche: string | null;
  last_language: string | null;
  uploaded_count: number;
}

export class PipelineState extends DurableObject {
  private sql: SqlStorage;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;

    // Initialise tables on first access.
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS quota (
        project TEXT PRIMARY KEY,
        units   INTEGER NOT NULL DEFAULT 0,
        day     TEXT    NOT NULL
      );
    `);

    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS rotation (
        id            INTEGER PRIMARY KEY CHECK (id = 1),
        last_niche    TEXT,
        last_language TEXT,
        uploaded_count INTEGER NOT NULL DEFAULT 0
      );
    `);

    this.sql.exec(`
      INSERT OR IGNORE INTO rotation (id, last_niche, last_language, uploaded_count)
      VALUES (1, NULL, NULL, 0);
    `);

    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS jobs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        niche        TEXT    NOT NULL,
        topic        TEXT    NOT NULL,
        language     TEXT    NOT NULL,
        status       TEXT    NOT NULL DEFAULT 'pending',
        video_key    TEXT,
        video_id     TEXT,
        project      TEXT,
        error        TEXT,
        created_at   TEXT    NOT NULL,
        updated_at   TEXT    NOT NULL
      );
    `);
  }

  // ─── Topic & Language Rotation ─────────────────────────────────────────────

  /**
   * Pick the next topic for the given niche, removing it from the queue.
   * Also rotates the language so consecutive videos alternate.
   */
  async pickNextJob(
    niches: string[],
    languages: string[],
    contentPlan: Record<string, { topics: string[] }>,
  ): Promise<ShortsJob | null> {
    const rot = this.getRotation();

    // Round-robin niches.
    let nicheIdx = 0;
    if (rot.last_niche && niches.includes(rot.last_niche)) {
      nicheIdx = (niches.indexOf(rot.last_niche) + 1) % niches.length;
    }
    const chosenNiche = niches[nicheIdx];

    // Round-robin languages.
    let langIdx = 0;
    if (rot.last_language && languages.includes(rot.last_language)) {
      langIdx = (languages.indexOf(rot.last_language) + 1) % languages.length;
    }
    const chosenLang = languages[langIdx];

    // Pop the first topic for this niche.
    const topics = contentPlan[chosenNiche]?.topics ?? [];
    if (topics.length === 0) {
      // Try the next niche that still has topics.
      for (let i = 1; i < niches.length; i++) {
        const alt = niches[(nicheIdx + i) % niches.length];
        const altTopics = contentPlan[alt]?.topics ?? [];
        if (altTopics.length > 0) {
          const topic = altTopics.shift()!;
          this.updateRotation(alt, chosenLang);
          return {
            niche: alt,
            topic,
            language: chosenLang,
            requestedAt: new Date().toISOString(),
          };
        }
      }
      return null; // all niches exhausted
    }

    const topic = topics.shift()!;
    this.updateRotation(chosenNiche, chosenLang);

    return {
      niche: chosenNiche,
      topic,
      language: chosenLang,
      requestedAt: new Date().toISOString(),
    };
  }

  private getRotation(): RotationRow {
    const cursor = this.sql.exec<RotationRow>(
      "SELECT last_niche, last_language, uploaded_count FROM rotation WHERE id = 1",
    );
    const rows = [...cursor];
    return rows[0] ?? { last_niche: null, last_language: null, uploaded_count: 0 };
  }

  private updateRotation(niche: string, language: string): void {
    this.sql.exec(
      "UPDATE rotation SET last_niche = ?, last_language = ?, uploaded_count = uploaded_count + 1 WHERE id = 1",
      niche,
      language,
    );
  }

  // ─── YouTube Quota Tracking ────────────────────────────────────────────────

  /**
   * Return the first project that still has quota left today.
   * Each upload costs 1,600 units; free tier is 10,000 units/day/project.
   */
  async pickYouTubeProject(projects: string[]): Promise<string | null> {
    const today = new Date().toISOString().slice(0, 10);

    for (const project of projects) {
      const row = this.getQuota(project);
      if (!row || row.day !== today) {
        // Fresh day → reset.
        this.sql.exec(
          "INSERT OR REPLACE INTO quota (project, units, day) VALUES (?, 0, ?)",
          project,
          today,
        );
        return project;
      }
      if (row.units + 1600 <= 10000) {
        return project;
      }
    }
    return null; // all projects exhausted
  }

  async recordUpload(project: string): Promise<void> {
    const today = new Date().toISOString().slice(0, 10);
    this.sql.exec(
      "UPDATE quota SET units = units + 1600 WHERE project = ? AND day = ?",
      project,
      today,
    );
  }

  private getQuota(project: string): QuotaRow | null {
    const cursor = this.sql.exec<QuotaRow>(
      "SELECT project, units, day FROM quota WHERE project = ?",
      project,
    );
    const rows = [...cursor];
    return rows[0] ?? null;
  }

  // ─── Job Logging ───────────────────────────────────────────────────────────

  async logJobStart(job: ShortsJob): Promise<number> {
    const now = new Date().toISOString();
    this.sql.exec(
      `INSERT INTO jobs (niche, topic, language, status, created_at, updated_at)
       VALUES (?, ?, ?, 'pending', ?, ?)`,
      job.niche,
      job.topic,
      job.language,
      now,
      now,
    );
    const cursor = this.sql.exec<{ id: number }>("SELECT last_insert_rowid() AS id");
    return [...cursor][0].id;
  }

  async logJobComplete(
    jobId: number,
    result: { success: boolean; videoKey?: string; videoId?: string; project?: string; error?: string },
  ): Promise<void> {
    this.sql.exec(
      `UPDATE jobs
       SET status = ?, video_key = ?, video_id = ?, project = ?, error = ?, updated_at = ?
       WHERE id = ?`,
      result.success ? "done" : "failed",
      result.videoKey ?? null,
      result.videoId ?? null,
      result.project ?? null,
      result.error ?? null,
      new Date().toISOString(),
      jobId,
    );
  }

  async getStats(): Promise<{ total: number; done: number; failed: number }> {
    const cursor = this.sql.exec<{ total: number; done: number; failed: number }>(
      `SELECT
         COUNT(*) AS total,
         SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done,
         SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
       FROM jobs`,
    );
    return [...cursor][0];
  }
}
