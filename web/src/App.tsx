// API 확인용 간단한 FE. 5단계 학습 흐름(코드 입력 → 흐름 설명 → 너라면? → 판정 → 제약 바꾸기)을
// 한 화면 상태로 돈다. 화면에 고정으로 두는 것은 안내·버튼 문구, 설명 수준 3개, 프리셋 4개, 5단계 이름뿐이다.
import { useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  MAX_CODE_CHARACTERS,
  redact,
  type AnalyzeResponse,
  type DecisionPoint,
  type Evidence,
  type FeedbackResponse,
  type Level,
  type OptionId,
  type Preset,
  type ScenarioResponse,
  type SecretFinding,
  type Verdict,
} from "./api/client";

type Stage = 0 | 1 | 2 | 3 | 4;
const STAGES = ["코드 입력", "흐름 설명", "너라면?", "판정", "제약 바꾸기"];
const LEVELS: [Level, string][] = [
  ["basic", "입문"],
  ["work", "실무"],
  ["deep", "심화"],
];
const PRESETS: [Preset, string][] = [
  ["latency", "tool 하나 3초 지연"],
  ["rateLimit", "rate limit 분당 60회"],
  ["traffic", "트래픽 10배"],
  ["team", "팀 3명"],
];
const LOADING_TEXT = ["코드 구조를 읽고 있어요", "공식 문서를 찾고 있어요", "결정 지점을 고르고 있어요"];

export default function App() {
  const [stage, setStage] = useState<Stage>(0);
  const [reached, setReached] = useState<Stage>(0);
  const [code, setCode] = useState("");
  const [focusLines, setFocusLines] = useState<number[]>([]);
  const [question, setQuestion] = useState("");
  const [level, setLevel] = useState<Level>("work");
  const [findings, setFindings] = useState<SecretFinding[]>([]);
  const [scanning, setScanning] = useState(false);

  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [selectedStep, setSelectedStep] = useState<number | null>(null);
  const [dpIndex, setDpIndex] = useState(0);
  const [choice, setChoice] = useState<OptionId | null>(null);
  const [reason, setReason] = useState("");
  const [showHint, setShowHint] = useState(false);
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});

  const [preset, setPreset] = useState<Preset | null>(null);
  const [scenario, setScenario] = useState<ScenarioResponse | null>(null);
  const [answer, setAnswer] = useState("");
  const [feedback, setFeedback] = useState<FeedbackResponse | null>(null);

  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<{ error: ApiError; retry: () => void } | null>(null);

  const lineCount = code ? code.split("\n").length : 0;
  const overLimit = code.length > MAX_CODE_CHARACTERS;
  const dps = analysis?.decisionPoints ?? [];
  const dp: DecisionPoint | undefined = dps[dpIndex];
  const verdict = dp ? verdicts[dp.id] : undefined;

  const go = (next: Stage) => {
    setStage(next);
    setReached((r) => (next > r ? next : r));
    setError(null);
  };

  async function run<T>(label: string, task: () => Promise<T>, retry: () => void): Promise<T | null> {
    setLoading(label);
    setError(null);
    try {
      return await task();
    } catch (e) {
      const error = e instanceof ApiError ? e : new ApiError(0, "unknown", String(e));
      if (error.code === "secrets_detected") {
        setFindings((error.details?.findings as SecretFinding[]) ?? []);
        go(0);
      }
      setError({ error, retry });
      return null;
    } finally {
      setLoading(null);
    }
  }

  // 붙여넣기 직후(디바운스) 비밀값 검사. LLM을 부르지 않는다.
  useEffect(() => {
    if (!code.trim() || overLimit) {
      setFindings([]);
      return;
    }
    setScanning(true);
    const timer = setTimeout(() => {
      api
        .scan(code)
        .then((r) => setFindings(r.findings))
        .catch(() => setFindings([]))
        .finally(() => setScanning(false));
    }, 400);
    return () => clearTimeout(timer);
  }, [code, overLimit]);

  const analyze = async () => {
    const result = await run(
      "분석",
      () => api.analyze({ code, level, focusLines, question }),
      analyze,
    );
    if (!result) return;
    setAnalysis(result);
    setVerdicts({});
    setDpIndex(0);
    setSelectedStep(null);
    setScenario(null);
    setFeedback(null);
    setPreset(null);
    setReached(1);
    go(1);
  };

  const submitJudge = async (skip: boolean) => {
    if (!dp || !analysis) return;
    const evidence = analysis.steps.find((s) => s.id === dp.stepId)?.evidence ?? [];
    const result = await run(
      "판정",
      () => api.judge({ decisionPoint: dp, evidence, choice: skip ? null : choice, reason: skip ? "" : reason, level }),
      () => submitJudge(skip),
    );
    if (!result) return;
    setVerdicts((v) => ({ ...v, [dp.id]: result }));
    go(3);
  };

  const startChallenge = (index: number) => {
    setDpIndex(index);
    setChoice(null);
    setReason("");
    setShowHint(false);
    go(2);
  };

  const skipToExplain = async (index: number) => {
    setDpIndex(index);
    setChoice(null);
    setReason("");
    // dpIndex 갱신 전이라 직접 판정을 부른다.
    const target = dps[index];
    const evidence = analysis!.steps.find((s) => s.id === target.stepId)?.evidence ?? [];
    const result = await run(
      "설명",
      () => api.judge({ decisionPoint: target, evidence, choice: null, reason: "", level }),
      () => skipToExplain(index),
    );
    if (!result) return;
    setVerdicts((v) => ({ ...v, [target.id]: result }));
    go(3);
  };

  const pickPreset = async (p: Preset) => {
    setPreset(p);
    setScenario(null);
    setFeedback(null);
    setAnswer("");
    const result = await run("질문 만들기", () => api.scenario(code, p, analysis?.steps ?? []), () => pickPreset(p));
    if (result) setScenario(result);
  };

  const submitFeedback = async () => {
    if (!preset || !scenario) return;
    const result = await run(
      "피드백",
      () => api.feedback(code, preset, scenario.question, answer),
      submitFeedback,
    );
    if (result) setFeedback(result);
  };

  const restart = () => {
    setCode("");
    setFocusLines([]);
    setQuestion("");
    setAnalysis(null);
    setVerdicts({});
    setReached(0);
    go(0);
  };

  const summary = useMemo(() => {
    const scored = Object.values(verdicts).filter((v) => !v.skipped);
    const hit = scored.reduce((n, v) => n + (v.hit ?? 0), 0);
    const total = scored.reduce((n, v) => n + v.total, 0);
    return { hit, miss: total - hit, answered: scored.length };
  }, [verdicts]);

  const stepLines = new Set(
    selectedStep != null ? analysis?.steps.find((s) => s.id === selectedStep)?.lines ?? [] : stage === 2 && dp ? dp.lines : [],
  );
  const warnLines = new Set(findings.map((f) => f.line));
  const focusSteps = analysis?.steps.filter((s) => s.lines.some((l) => focusLines.includes(l))) ?? [];

  return (
    <div className="app">
      <header className="header">
        <strong>AI Code Decoder</strong>
        <span className="muted">API 확인용 화면</span>
      </header>

      <nav className="stepnav" aria-label="학습 단계">
        {STAGES.map((name, i) => {
          const state = i === stage ? "current" : i <= reached ? "done" : "todo";
          return (
            <button
              key={name}
              className={`stepnav-item ${state}`}
              disabled={i > reached || loading !== null}
              aria-current={i === stage ? "step" : undefined}
              onClick={() => go(i as Stage)}
            >
              <span className="num">{i + 1}</span> {name}
            </button>
          );
        })}
      </nav>

      {loading && (
        <div className="banner info" role="status">
          {loading === "분석" ? <LoadingText /> : `${loading} 중이에요…`}
        </div>
      )}
      {error && (
        <div className="banner error" role="alert">
          <div>
            <strong>{error.error.message}</strong> <code className="muted">{error.error.code}</code>
            {error.error.code === "syntax_error" && error.error.details?.line != null && (
              <div>{String(error.error.details.line)}번째 줄을 확인해 주세요.</div>
            )}
          </div>
          {!["secrets_detected", "syntax_error", "input_invalid", "language_unsupported"].includes(error.error.code) && (
            <button onClick={error.retry}>다시 시도</button>
          )}
        </div>
      )}

      <main className="main">
        {stage === 0 && (
          <div className="two-col">
            <section>
              <label htmlFor="code" className="label">
                코드 붙여넣기 <span className="pill">Python만 지원</span>
              </label>
              <textarea
                id="code"
                className="code-input"
                value={code}
                spellCheck={false}
                placeholder="AI가 짜 준 Python 코드를 붙여넣어 주세요"
                onChange={(e) => {
                  setCode(e.target.value);
                  setFocusLines([]);
                }}
              />
              <div className={`muted ${overLimit ? "danger" : ""}`}>
                {lineCount}줄 · {code.length.toLocaleString()} / {MAX_CODE_CHARACTERS.toLocaleString()}자
                {overLimit && " — 한도를 넘었어요. 코드를 줄여 주세요."}
              </div>
              {code && (
                <>
                  <p className="label">궁금한 줄을 눌러 표시하세요</p>
                  <CodeView
                    code={code}
                    focus={new Set(focusLines)}
                    step={new Set()}
                    warn={warnLines}
                    onLineClick={(n) =>
                      setFocusLines((f) => (f.includes(n) ? f.filter((x) => x !== n) : [...f, n].sort((a, b) => a - b)))
                    }
                  />
                </>
              )}
            </section>
            <section className="panel">
              {findings.length > 0 && (
                <div className="banner warn" role="alert">
                  <div>
                    API 키처럼 보이는 값이 {findings.length}개 있어요(
                    {findings.map((f) => `${f.line}번째 줄 ${f.kind}`).join(", ")}). 가리기 전에는 분석할 수 없어요.
                  </div>
                  <button onClick={() => setCode(redact(code, findings))}>값 가리기</button>
                </div>
              )}
              <div className="chips">
                {focusLines.map((n) => (
                  <span key={n} className="chip">
                    {n}번째 줄 · <code>{code.split("\n")[n - 1]?.trim().slice(0, 30)}</code>
                  </span>
                ))}
              </div>
              <label htmlFor="question" className="label">
                질문 (비우면 코드 전체 흐름을 설명해요)
              </label>
              <textarea id="question" rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} />
              <fieldset>
                <legend className="label">설명 수준</legend>
                {LEVELS.map(([value, name]) => (
                  <label key={value} className="radio">
                    <input type="radio" name="level" checked={level === value} onChange={() => setLevel(value)} />
                    {name}
                  </label>
                ))}
              </fieldset>
              <p className="muted small">
                코드는 실행하지 않고, 분석을 위해 OpenAI API로 전송돼요. 앱에는 저장하지 않아요.
              </p>
              <p className="muted small" style={{ opacity: 0.6 }}>
                내 시스템 맥락 입력은 확장 기능이에요.
              </p>
              <button
                className="primary"
                disabled={!code.trim() || overLimit || findings.length > 0 || scanning || loading !== null}
                onClick={analyze}
              >
                분석 시작
              </button>
            </section>
          </div>
        )}

        {stage === 1 && analysis && (
          <div className="two-col">
            <section>
              <p className="muted small">
                분석 언어: {analysis.language} · <strong>정적 분석 · 실제 실행 결과가 아니에요</strong>
              </p>
              <CodeView code={code} focus={new Set(focusLines)} step={stepLines} warn={new Set()} />
            </section>
            <section className="panel">
              {focusSteps.length > 0 && (
                <p className="banner info">
                  질문한 줄은 {focusSteps.map((s) => `${s.id}단계(${s.title})`).join(", ")}에 있어요.
                </p>
              )}
              {analysis.steps.map((s) => (
                <button
                  key={s.id}
                  className={`card step ${selectedStep === s.id ? "selected" : ""}`}
                  onClick={() => setSelectedStep(selectedStep === s.id ? null : s.id)}
                >
                  <div className="row">
                    <strong>
                      {s.id}. {s.title}
                    </strong>
                    {s.isDecision && <span className="badge decision">결정 지점</span>}
                  </div>
                  <div className="muted small">
                    <code>{s.symbol}</code> · {s.lines.join(", ")}번째 줄
                  </div>
                  <p>{s.summary}</p>
                  {s.io && (
                    <p className="small">
                      입력: {s.io.in} → 출력: {s.io.out}
                    </p>
                  )}
                  <div className="badges">
                    {s.evidence?.map((e, i) => <EvidenceBadge key={i} evidence={e} />)}
                  </div>
                </button>
              ))}
              <div className="actions">
                {dps.length > 0 ? (
                  <>
                    <button className="primary" onClick={() => startChallenge(0)}>
                      너라면? ({dps.length}문제)
                    </button>
                    <button onClick={() => skipToExplain(0)}>건너뛰고 설명 보기</button>
                  </>
                ) : (
                  <button className="primary" onClick={() => go(4)}>
                    제약 바꿔 보기
                  </button>
                )}
              </div>
              <details className="muted small">
                <summary>에이전트 도구 호출 {analysis.meta.toolCalls?.length ?? 0}회 ({analysis.meta.model})</summary>
                <ul>
                  {analysis.meta.toolCalls?.map((t, i) => (
                    <li key={i}>
                      {t.name}({t.args ? Object.values(t.args).join(", ") : ""})
                    </li>
                  ))}
                </ul>
              </details>
            </section>
          </div>
        )}

        {stage === 2 && dp && (
          <div className="two-col">
            <section>
              <p className="muted small">
                결정 지점 {dpIndex + 1}/{dps.length} · {dp.lines.join(", ")}번째 줄
              </p>
              <CodeView code={code} focus={new Set()} step={new Set(dp.lines)} warn={new Set()} />
            </section>
            <section className="panel">
              <h2>{dp.prompt}</h2>
              <div role="radiogroup" aria-label="선택지">
                {dp.options.map((o) => (
                  <label key={o.id} className={`card option ${choice === o.id ? "selected" : ""}`}>
                    <input type="radio" name="option" checked={choice === o.id} onChange={() => setChoice(o.id)} />
                    <strong>
                      {o.id}. {o.label}
                    </strong>
                    {o.detail && <div className="muted small">{o.detail}</div>}
                  </label>
                ))}
              </div>
              <label htmlFor="reason" className="label">
                이유
              </label>
              <textarea id="reason" rows={4} value={reason} onChange={(e) => setReason(e.target.value)} />
              <button className="link" onClick={() => setShowHint(!showHint)} aria-expanded={showHint}>
                힌트 {showHint ? "숨기기" : "보기"}
              </button>
              {showHint && <p className="banner info">{dp.hint}</p>}
              <div className="actions">
                <button className="primary" disabled={!choice || loading !== null} onClick={() => submitJudge(false)}>
                  답 제출
                </button>
                <button disabled={loading !== null} onClick={() => submitJudge(true)}>
                  건너뛰고 설명 보기
                </button>
              </div>
            </section>
          </div>
        )}

        {stage === 3 && dp && verdict && (
          <section className="panel wide">
            {verdict.skipped ? (
              <p className="banner info">설명 모드예요. 이 결정 지점은 학습 정리 집계에서 빠져요.</p>
            ) : (
              <h2>
                조건 {verdict.total}개 중 {verdict.hit}개를 짚었어요
              </h2>
            )}
            <div className="two-col">
              {verdict.mine && (
                <div className="card">
                  <div className="muted small">내 답</div>
                  <strong>
                    {verdict.mine.choice}. {dp.options.find((o) => o.id === verdict.mine!.choice)?.label}
                  </strong>
                  <p>{verdict.mine.reason || "(이유 없음)"}</p>
                </div>
              )}
              <div className="card">
                <div className="muted small">이 코드의 선택</div>
                <strong>{verdict.codeChoice.label}</strong>
                <p>{verdict.codeChoice.summary}</p>
                <EvidenceBadge evidence={verdict.codeChoice.intent} />
              </div>
            </div>
            <h3>결정을 가른 조건</h3>
            {verdict.conditions.map((c) => {
              const condition = dp.conditions.find((x) => x.id === c.id);
              return (
                <div key={c.id} className={`card condition ${c.status ?? ""}`}>
                  <div className="row">
                    <strong>{condition?.label ?? c.id}</strong>
                    {c.status && <span className={`badge ${c.status}`}>{c.status === "hit" ? "짚음" : "놓침"}</span>}
                  </div>
                  <p>{c.note}</p>
                </div>
              );
            })}
            <h3>방식별 트레이드오프</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>방식</th>
                    <th>기다리는 시간</th>
                    <th>실패 시 동작</th>
                    <th>복잡도</th>
                  </tr>
                </thead>
                <tbody>
                  {verdict.tradeoffs.rows.map((r) => (
                    <tr key={r.approach} className={r.isThisCode ? "this-code" : ""}>
                      <td>
                        {r.approach} {r.isThisCode && <span className="badge decision">이 코드</span>}
                      </td>
                      <td>{r.waitTime}</td>
                      <td>{r.onFailure}</td>
                      <td>{r.complexity}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <EvidenceBadge evidence={verdict.tradeoffs.evidence} />
            <div className="actions">
              {dpIndex + 1 < dps.length ? (
                <button className="primary" onClick={() => startChallenge(dpIndex + 1)}>
                  다음 너라면? ({dpIndex + 2}/{dps.length})
                </button>
              ) : (
                <button className="primary" onClick={() => go(4)}>
                  제약 바꿔 보기
                </button>
              )}
            </div>
          </section>
        )}

        {stage === 4 && analysis && (
          <section className="panel wide">
            <h2>조건 하나를 바꿔 볼까요?</h2>
            <div className="chips">
              {PRESETS.map(([value, name]) => (
                <button
                  key={value}
                  className="chip-button"
                  aria-pressed={preset === value}
                  disabled={loading !== null}
                  onClick={() => pickPreset(value)}
                >
                  {name}
                </button>
              ))}
            </div>
            {scenario && (
              <>
                <p className="question">{scenario.question}</p>
                <p className="muted small">{scenario.followUp}</p>
                <label htmlFor="answer" className="label">
                  내 답
                </label>
                <textarea id="answer" rows={4} value={answer} onChange={(e) => setAnswer(e.target.value)} />
                <button className="primary" disabled={!answer.trim() || loading !== null} onClick={submitFeedback}>
                  답 제출
                </button>
              </>
            )}
            {feedback && (
              <div className="card">
                <strong>{feedback.verdict}</strong>
                {feedback.points.length > 0 && (
                  <>
                    <div className="muted small">잘한 점</div>
                    <ul>
                      {feedback.points.map((p) => (
                        <li key={p}>{p}</li>
                      ))}
                    </ul>
                  </>
                )}
                <div className="muted small">더 생각해 볼 것</div>
                <p>{feedback.further}</p>
              </div>
            )}
            <div className="card summary">
              <strong>이번 학습 정리</strong>
              <p>
                답한 결정 지점 {summary.answered}개 · 짚은 조건 {summary.hit}개 · 놓친 조건 {summary.miss}개
              </p>
              <button onClick={restart}>새 코드로 시작</button>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

function LoadingText() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setI((n) => (n + 1) % LOADING_TEXT.length), 4000);
    return () => clearInterval(timer);
  }, []);
  return <>{LOADING_TEXT[i]}… (10~40초 걸릴 수 있어요)</>;
}

function CodeView(props: {
  code: string;
  focus: Set<number>;
  step: Set<number>;
  warn: Set<number>;
  onLineClick?: (line: number) => void;
}) {
  const lines = props.code.split("\n");
  return (
    <pre className="codeview">
      {lines.map((text, i) => {
        const n = i + 1;
        const cls = [props.warn.has(n) && "warn", props.step.has(n) && "step", props.focus.has(n) && "focus"]
          .filter(Boolean)
          .join(" ");
        return (
          <div key={n} className={`line ${cls}`}>
            {props.onLineClick ? (
              <button className="gutter" onClick={() => props.onLineClick!(n)} aria-label={`${n}번째 줄 선택`}>
                {n}
              </button>
            ) : (
              <span className="gutter">{n}</span>
            )}
            <code>{text || " "}</code>
          </div>
        );
      })}
    </pre>
  );
}

const BADGE_LABEL: Record<Evidence["badge"], string> = {
  official: "공식 문서 확인",
  inferred: "추정",
  needs_check: "확인 필요",
};

function EvidenceBadge({ evidence }: { evidence: Evidence }) {
  return (
    <span className={`evidence ${evidence.badge}`}>
      <span className="badge">{BADGE_LABEL[evidence.badge]}</span>
      {evidence.symbol && <code>{evidence.symbol}</code>}
      {evidence.url && (
        <a href={evidence.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
          문서
        </a>
      )}
      {evidence.note && <span className="muted small"> {evidence.note}</span>}
    </span>
  );
}
