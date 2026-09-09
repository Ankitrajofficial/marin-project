/**
 * Optional LLM layer.
 *
 * ORCA uses a language model for exactly two jobs: parsing intent (planner) and writing the
 * final explanation (synthesis). It is never used for hazard classification - see risk.ts.
 *
 * If ANTHROPIC_API_KEY is absent, every function here reports unavailable and the callers
 * fall back to deterministic rules and templates. The demo must never depend on a key.
 */
import Anthropic from "@anthropic-ai/sdk";

export const MODEL = "claude-sonnet-5";

let client: Anthropic | null = null;

export function llmAvailable(): boolean {
  return Boolean(process.env.ANTHROPIC_API_KEY);
}

function getClient(): Anthropic | null {
  if (!llmAvailable()) return null;
  if (!client) client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY! });
  return client;
}

export interface LlmOutcome {
  ok: boolean;
  text: string;
  error?: string;
  durationMs: number;
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
}

export async function llmComplete(
  system: string,
  user: string,
  opts: { maxTokens?: number; timeoutMs?: number; temperature?: number } = {},
): Promise<LlmOutcome> {
  const t0 = Date.now();
  const c = getClient();
  if (!c) return { ok: false, text: "", error: "ANTHROPIC_API_KEY not set", durationMs: 0 };
  try {
    const res = await c.messages.create(
      {
        model: MODEL,
        max_tokens: opts.maxTokens ?? 900,
        temperature: opts.temperature ?? 0.2,
        system,
        messages: [{ role: "user", content: user }],
      },
      { timeout: opts.timeoutMs ?? 12000 },
    );
    const text = res.content
      .filter((b): b is Anthropic.TextBlock => b.type === "text")
      .map((b) => b.text)
      .join("");
    return {
      ok: true, text, durationMs: Date.now() - t0, model: res.model,
      inputTokens: res.usage.input_tokens, outputTokens: res.usage.output_tokens,
    };
  } catch (err) {
    return {
      ok: false, text: "",
      error: err instanceof Error ? err.message : String(err),
      durationMs: Date.now() - t0,
    };
  }
}

/** Pull the first JSON object out of a model response, tolerating fenced code blocks. */
export function extractJson<T>(text: string): T | null {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  const candidate = (fenced ? fenced[1] : text).trim();
  const start = candidate.indexOf("{");
  const end = candidate.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  try {
    return JSON.parse(candidate.slice(start, end + 1)) as T;
  } catch {
    return null;
  }
}
