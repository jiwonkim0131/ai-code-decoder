// /api/* 호출. 타입은 contract/openapi.json에서 생성한 schema.ts만 쓴다(손으로 쓰지 않음).
import type { components } from "./schema";

type S = components["schemas"];
export type AnalyzeRequest = S["AnalyzeRequest"];
export type AnalyzeResponse = S["AnalyzeResponse"];
export type Step = S["Step"];
export type Evidence = S["Evidence"];
export type DecisionPoint = S["DecisionPoint"];
export type SecretFinding = S["SecretFinding"];
export type Verdict = S["Verdict"];
export type JudgeRequest = S["JudgeRequest"];
export type ScenarioResponse = S["ScenarioResponse"];
export type FeedbackResponse = S["FeedbackResponse"];
export type SummaryRequest = S["SummaryRequest"];
export type SummaryResponse = S["SummaryResponse"];
export type Preset = ScenarioResponse["preset"];
export type Level = NonNullable<AnalyzeRequest["level"]>;
export type OptionId = DecisionPoint["codeOption"];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "서버에 연결하지 못했어요.");
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = data?.error;
    if (error) throw new ApiError(response.status, error.code, error.message, error.details);
    if (response.status === 504) {
      throw new ApiError(504, "timeout", "시간이 오래 걸려요. 코드를 줄여 다시 시도해 주세요.");
    }
    throw new ApiError(response.status, "unknown", "알 수 없는 오류가 났어요.");
  }
  return data as T;
}

// 같은 입력이면 같은 질문·모범 답변을 다시 써도 되므로 브라우저에 오래 둔다(새로고침·처음으로 돌아가도 유지).
// 저장소를 못 쓰는 환경(시크릿 창 등)에서는 캐시 없이 그냥 요청한다.
const CACHE_PREFIX = "api-cache:";
const CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000;

async function cacheKey(path: string, body: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(path + JSON.stringify(body));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  return CACHE_PREFIX + hex;
}

function pruneExpired(now: number) {
  for (let i = localStorage.length - 1; i >= 0; i--) {
    const key = localStorage.key(i);
    if (!key?.startsWith(CACHE_PREFIX)) continue;
    try {
      if (JSON.parse(localStorage.getItem(key) ?? "null")?.expires > now) continue;
    } catch {
      // 깨진 항목은 지운다
    }
    localStorage.removeItem(key);
  }
}

async function cachedPost<T>(path: string, body: unknown): Promise<T> {
  let key: string | null = null;
  try {
    key = await cacheKey(path, body);
    const hit = JSON.parse(localStorage.getItem(key) ?? "null");
    if (hit && hit.expires > Date.now()) return hit.data as T;
  } catch {
    // 캐시를 못 읽으면 요청으로 넘어간다
  }
  const data = await post<T>(path, body);
  if (key) {
    try {
      const now = Date.now();
      pruneExpired(now);
      localStorage.setItem(key, JSON.stringify({ expires: now + CACHE_TTL_MS, data }));
    } catch {
      // 용량 초과 등은 무시한다
    }
  }
  return data;
}

export const api = {
  scan: (code: string) => post<S["ScanResponse"]>("/api/scan", { code }),
  analyze: (body: AnalyzeRequest) => post<AnalyzeResponse>("/api/analyze", body),
  judge: (body: JudgeRequest) => post<Verdict>("/api/judge", body),
  scenario: (code: string, preset: Preset, steps: Step[]) =>
    cachedPost<ScenarioResponse>("/api/scenario", { code, preset, steps }),
  feedback: (code: string, preset: Preset, question: string, answer: string) =>
    post<FeedbackResponse>("/api/feedback", { code, preset, question, answer }),
  modelAnswer: (code: string, preset: Preset, question: string) =>
    cachedPost<S["ModelAnswerResponse"]>("/api/model-answer", { code, preset, question }),
  // 같은 학습 기록이면 같은 정리를 쓴다(기록이 바뀌면 키가 달라져 새로 만든다).
  summary: (body: SummaryRequest) => cachedPost<SummaryResponse>("/api/summary", body),
};

export const MAX_CODE_CHARACTERS = 20_000;
export const REDACTED = "<REDACTED>";

/** "값 가리기": findings의 (line, start, end) 범위를 REDACTED로 치환한다(서버 secrets.redact와 같은 규칙). */
export function redact(code: string, findings: SecretFinding[]): string {
  const lines = code.split("\n");
  const sorted = [...findings].sort((a, b) => a.line - b.line || b.start - a.start);
  for (const f of sorted) {
    const line = lines[f.line - 1];
    lines[f.line - 1] = line.slice(0, f.start) + REDACTED + line.slice(f.end);
  }
  return lines.join("\n");
}
