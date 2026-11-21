// API 확인용 간단한 FE. 6단계 학습 흐름(코드 입력 → 코드 분석 → 설계 선택 → 설계 비교 → 설계 응용 → 학습 정리)을
// 한 화면 상태로 돈다. 화면에 고정으로 두는 것은 안내·버튼 문구, 설명 수준 3개, 프리셋 4개, 6단계 이름뿐이다.
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
  type Step,
  type SummaryResponse,
  type Verdict,
} from "./api/client";
import { Select } from "./components/Select";
import { formatPasted, INDENT, leadingSpaces, nextIndent, reindent } from "./editing";
import { buildHtml, buildMarkdown, formatLines, reportFileName, type ReportData } from "./report";

type Stage = 0 | 1 | 2 | 3 | 4 | 5;
type PresetWork = {
  scenario: ScenarioResponse;
  answer: string;
  feedback: FeedbackResponse | null;
  modelAnswer: string | null; // 처음 볼 때 한 번만 받아 둔다
  showModel: boolean; // 모범 답변을 펼쳤는지
};
type StepState = "current" | "done" | "skipped" | "todo";
const STEP_STATE_LABEL: Record<StepState, string> = {
  current: "현재 단계",
  done: "완료",
  skipped: "건너뜀",
  todo: "아직 진행하지 않음",
};
const STAGES = ["코드 입력", "코드 분석", "설계 선택", "설계 비교", "설계 응용", "학습 정리"];
// 단계 설명은 넓은 화면에서 두 줄 안에 들어가게 쓴다(헤더 높이를 두 줄로 고정한다).
const STAGE_DESCRIPTIONS = [
  "이해가 필요한 코드를 붙여넣고, 원하는 설명 수준을 선택하세요.",
  "정적 분석 결과를 기반으로 문법과 실행 흐름을 나눠 설명해요. 카드를 누르면 해당 코드가 강조돼요.\n설계 선택에서 다른 설계 방식을 직접 골라 보거나, 설계 비교로 바로 넘어갈 수 있어요.",
  "이 설계 포인트에서 나라면 어떤 방식을 골랐을지 선택하고, 그 이유를 적어 보세요.",
  "내가 선택한 방식이 설계 시 고려해야 할 판단 기준을 얼마나 반영했는지 확인하고, 다른 방식들과 비교해 보세요.",
  "제약 조건이 변경되었을 때 이 설계에 어떤 변화가 생길지 고민해 보세요.",
  "지금까지 학습한 내용 중 핵심을 정리했어요. HTML 보고서나 Markdown으로 받아 보관할 수 있어요.",
];
// 서버가 분석할 수 있는 언어. 버전은 서버의 AST 파서 기준이다.
const LANGUAGES = [{ id: "python", name: "Python", version: "3.12" }];
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
// 분석은 응답을 한 번에 받으므로 진행 단계는 보통 걸리는 시간에 맞춘 안내다(마지막 단계에서 멈춘다).
const ANALYZE_STEPS: [string, number][] = [
  ["코드 구조를 읽고 있어요", 0],
  ["공식 문서를 찾고 있어요", 4],
  ["설계 포인트를 고르고 있어요", 14],
];

export default function App() {
  const [stage, setStage] = useState<Stage>(0);
  const [reached, setReached] = useState<Stage>(0);
  const [code, setCode] = useState("");
  const [language, setLanguage] = useState(LANGUAGES[0].id);
  const [focusLines, setFocusLines] = useState<number[]>([]);
  const [question, setQuestion] = useState("");
  const [level, setLevel] = useState<Level>("work");
  const [findings, setFindings] = useState<SecretFinding[]>([]);
  const [guideOpen, setGuideOpen] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [secretsDialog, setSecretsDialog] = useState(false);

  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [selectedStep, setSelectedStep] = useState<number | null>(null);
  const [dpIndex, setDpIndex] = useState(0);
  const [choice, setChoice] = useState<OptionId | null>(null);
  const [reason, setReason] = useState("");
  const [showHint, setShowHint] = useState(false);
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});

  const [preset, setPreset] = useState<Preset | null>(null);
  // 제약마다 질문·답·피드백을 따로 둬서, 제약을 오가도 이어서 쓰고 각각 제출할 수 있다.
  const [applied, setApplied] = useState<Partial<Record<Preset, PresetWork>>>({});
  const [restartDialog, setRestartDialog] = useState(false);
  const [report, setReport] = useState<ReportData | null>(null);

  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<{ error: ApiError; retry: () => void } | null>(null);

  const overLimit = code.length > MAX_CODE_CHARACTERS;
  const dps = analysis?.decisionPoints ?? [];
  const dp: DecisionPoint | undefined = dps[dpIndex];
  const verdict = dp ? verdicts[dp.id] : undefined;
  // 트레이드오프 표에서 내가 고른 방식의 행을 칠할 때 쓴다(건너뛰었으면 없음).
  const mineLabel = verdict?.mine && dp?.options.find((o) => o.id === verdict.mine!.choice)?.label;

  // 단계 버튼은 보여 줄 내용이 있을 때만 연다(설계 포인트가 없거나 비교 전이면 3·4단계는 닫힘).
  const available = (i: number) =>
    i === 0 ||
    (i === 1 && analysis !== null) ||
    (i === 2 && dp !== undefined && reached >= 2) ||
    (i === 3 && verdict !== undefined) ||
    (i === 4 && analysis !== null && reached >= 4) ||
    (i === 5 && report !== null);

  // 단계 표시 상태: 현재 / 완료(그 단계의 일을 끝냄) / 건너뜀(이미 지나쳤지만 끝내지 않음) / 아직 안 감.
  const completed = (i: number) =>
    i === 0
      ? analysis !== null
      : i === 1
        ? analysis !== null && reached >= 2
        : i === 2
          ? Object.values(verdicts).some((v) => !v.skipped)
          : i === 3
            ? Object.keys(verdicts).length > 0
            : i === 4
              ? Object.values(applied).some((w) => w?.feedback)
              : report !== null;
  const stepState = (i: number): StepState =>
    i === stage ? "current" : completed(i) ? "done" : i < reached ? "skipped" : "todo";

  const go = (next: Stage) => {
    setStage(next);
    setReached((r) => (next > r ? next : r));
    setError(null);
    setShowHint(false);
  };

  async function run<T>(label: string, task: () => Promise<T>, retry: () => void): Promise<T | null> {
    setLoading(label);
    setError(null);
    try {
      return await task();
    } catch (e) {
      const error = e instanceof ApiError ? e : new ApiError(0, "unknown", String(e));
      if (error.code === "secrets_detected") {
        // 서버에서 걸린 경우도 오류 배너 대신 같은 확인 모달로 안내한다.
        setFindings((error.details?.findings as SecretFinding[]) ?? []);
        go(0);
        setSecretsDialog(true);
        return null;
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

  // 비밀값이 있으면 바로 보내지 않고 "값 가리고 진행하기" 모달을 띄운다.
  const requestAnalyze = () => {
    if (findings.length > 0) setSecretsDialog(true);
    else analyze(code);
  };

  const redactAndAnalyze = () => {
    const redacted = redact(code, findings);
    setSecretsDialog(false);
    setCode(redacted);
    setFindings([]);
    analyze(redacted);
  };

  const analyze = async (source: string) => {
    const result = await run(
      "분석",
      () => api.analyze({ code: source, language, level, focusLines, question }),
      () => analyze(source),
    );
    if (!result) return;
    setAnalysis(result);
    setVerdicts({});
    setDpIndex(0);
    setSelectedStep(null);
    setApplied({});
    setPreset(null);
    setReport(null);
    setReached(1);
    go(1);
  };

  const submitJudge = async (skip: boolean) => {
    if (!dp || !analysis) return;
    const evidence = analysis.steps.find((s) => s.id === dp.stepId)?.evidence ?? [];
    const result = await run(
      "설계 비교",
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
      "설계 비교",
      () => api.judge({ decisionPoint: target, evidence, choice: null, reason: "", level }),
      () => skipToExplain(index),
    );
    if (!result) return;
    setVerdicts((v) => ({ ...v, [target.id]: result }));
    go(3);
  };

  const work = preset ? applied[preset] : undefined;
  const updateWork = (p: Preset, patch: Partial<PresetWork>) =>
    setApplied((a) => ({ ...a, [p]: { ...a[p]!, ...patch } }));

  const pickPreset = async (p: Preset) => {
    setPreset(p);
    setShowHint(false);
    if (applied[p]) return; // 한 번 만든 질문과 쓰던 답은 그대로 둔다
    const result = await run("질문 만들기", () => api.scenario(code, p, analysis?.steps ?? []), () => pickPreset(p));
    if (result)
      setApplied((a) => ({ ...a, [p]: { scenario: result, answer: "", feedback: null, modelAnswer: null, showModel: false } }));
  };

  // 제출한 뒤에도 답을 고쳐 다시 제출할 수 있고, 피드백은 마지막 제출 기준으로 바뀐다.
  const submitFeedback = async () => {
    if (!preset || !work) return;
    const p = preset;
    const result = await run(
      "피드백",
      () => api.feedback(code, p, work.scenario.question, work.answer),
      submitFeedback,
    );
    if (result) updateWork(p, { feedback: result });
  };

  // 모범 답변은 처음 펼칠 때만 만들고, 이후에는 받아 둔 것을 여닫는다.
  const toggleModelAnswer = async () => {
    if (!preset || !work) return;
    const p = preset;
    if (work.modelAnswer !== null) {
      updateWork(p, { showModel: !work.showModel });
      return;
    }
    const result = await run(
      "모범 답변",
      () => api.modelAnswer(code, p, work.scenario.question),
      toggleModelAnswer,
    );
    if (result) updateWork(p, { modelAnswer: result.answer, showModel: true });
  };

  // 학습 정리: 1~5단계 기록을 모아 핵심 정리만 LLM에 맡기고, 문서는 기록으로 조립한다.
  // 같은 기록이면 캐시된 정리를 쓰므로 단계를 오가도 다시 만들지 않는다.
  const openSummary = async () => {
    if (!analysis) return;
    const decisions = dps.filter((d) => verdicts[d.id]).map((d) => ({ dp: d, verdict: verdicts[d.id] }));
    const applications = PRESETS.flatMap(([p, presetName]) => {
      const w = applied[p];
      return w
        ? [{ preset: p, presetName, scenario: w.scenario, answer: w.answer, feedback: w.feedback, modelAnswer: w.modelAnswer }]
        : [];
    });
    const summary: SummaryResponse | null = await run(
      "학습 정리",
      () =>
        api.summary({
          steps: analysis.steps,
          decisions: decisions.map(({ dp, verdict }) => ({ decisionPoint: dp, verdict })),
          applications: applications.map((a) => ({
            preset: a.preset,
            question: a.scenario.question,
            answer: a.answer,
            feedback: a.feedback,
            modelAnswer: a.modelAnswer,
          })),
        }),
      openSummary,
    );
    if (!summary) return;
    setReport({ createdAt: new Date(), languageName: language_.name, code, analysis, decisions, applications, summary });
    go(5);
  };

  const download = (ext: "md" | "html") => {
    if (!report) return;
    const text = ext === "md" ? buildMarkdown(report) : buildHtml(report);
    const url = URL.createObjectURL(new Blob([text], { type: ext === "md" ? "text/markdown;charset=utf-8" : "text/html;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = reportFileName(report, ext);
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  const reportHtml = useMemo(() => (report ? buildHtml(report) : ""), [report]);

  const restart = () => {
    setRestartDialog(false);
    setCode("");
    setFocusLines([]);
    setQuestion("");
    setAnalysis(null);
    setVerdicts({});
    setApplied({});
    setPreset(null);
    setReport(null);
    setReached(0);
    go(0);
  };

  const stepLines = new Set(
    selectedStep != null ? analysis?.steps.find((s) => s.id === selectedStep)?.lines ?? [] : stage === 2 && dp ? dp.lines : [],
  );
  // 문법 오류 줄도 비밀값 줄과 같이 입력창에서 빨갛게 짚는다.
  const syntaxLine =
    error?.error.code === "syntax_error" && error.error.details?.line != null ? Number(error.error.details.line) : null;
  const warnLines = new Set([...findings.map((f) => f.line), ...(syntaxLine ? [syntaxLine] : [])]);
  const focusSteps = analysis?.steps.filter((s) => s.lines.some((l) => focusLines.includes(l))) ?? [];
  const language_ = LANGUAGES.find((l) => l.id === language) ?? LANGUAGES[0];
  const busy = loading !== null;
  const noChallenge = (stage === 2 || stage === 3) && !(stage === 2 ? dp : dp && verdict);

  // 다음 단계로 가는 버튼은 단계마다 페이지 제목 오른쪽 같은 자리에 둔다(주 버튼이 맨 오른쪽).
  let footer: ReactNode = null;
  if (stage === 0) {
    footer = (
      <button
        className="primary"
        disabled={!code.trim() || overLimit || scanning || busy}
        onClick={requestAnalyze}
      >
        분석 시작
      </button>
    );
  } else if (stage === 1 && analysis) {
    footer =
      dps.length > 0 ? (
        <>
          <button disabled={busy} onClick={() => skipToExplain(0)}>
            설계 비교 바로 보기
          </button>
          <button className="primary" disabled={busy} onClick={() => startChallenge(0)}>
            설계 선택
          </button>
        </>
      ) : (
        <button className="primary" onClick={() => go(4)}>
          설계 응용
        </button>
      );
  } else if (noChallenge) {
    footer = (
      <>
        <button onClick={() => go(1)}>코드 분석으로</button>
        {dps.length === 0 && (
          <button className="primary" onClick={() => go(4)}>
            설계 응용
          </button>
        )}
      </>
    );
  } else if (stage === 2) {
    footer = (
      <>
        <button disabled={busy} onClick={() => submitJudge(true)}>
          설계 비교 바로 보기
        </button>
        <button className="primary" disabled={!choice || busy} onClick={() => submitJudge(false)}>
          제출
        </button>
      </>
    );
  } else if (stage === 3) {
    footer =
      dpIndex + 1 < dps.length ? (
        <button className="primary" onClick={() => startChallenge(dpIndex + 1)}>
          다음 설계 포인트 ({dpIndex + 2}/{dps.length})
        </button>
      ) : (
        <button className="primary" onClick={() => go(4)}>
          설계 응용
        </button>
      );
  } else if (stage === 4) {
    footer = (
      <button className="primary" disabled={busy} onClick={openSummary}>
        학습 정리
      </button>
    );
  } else if (stage === 5 && report) {
    footer = (
      <>
        <button onClick={() => download("md")}>Markdown 받기</button>
        <button className="primary" onClick={() => download("html")}>
          HTML 보고서 받기
        </button>
      </>
    );
  }

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          {/* 코드 입력 단계가 아니면 로고로 처음으로 돌아갈 수 있다(학습 내용이 사라지므로 확인 모달을 거친다). */}
          {stage === 0 ? (
            <div className="brand">
              <span className="brand-mark" aria-hidden="true">
                &lt;/&gt;
              </span>
              <strong>AI Code Decoder</strong>
            </div>
          ) : (
            <button className="brand brand-button" aria-label="코드 입력으로 돌아가기" onClick={() => setRestartDialog(true)}>
              <span className="brand-mark" aria-hidden="true">
                &lt;/&gt;
              </span>
              <strong>AI Code Decoder</strong>
            </button>
          )}
          <nav className="stepnav" aria-label="학습 단계">
            {STAGES.map((name, i) => {
              const state = stepState(i);
              return (
                <Fragment key={name}>
                  {i > 0 && (
                    <svg className="stepnav-sep" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                      <path d="M5.5 3.5 9 7l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                    </svg>
                  )}
                  <button
                    className={`stepnav-item ${state}`}
                    disabled={!available(i) || loading !== null}
                    aria-current={state === "current" ? "step" : undefined}
                    title={STEP_STATE_LABEL[state]}
                    onClick={() => (i === 5 ? openSummary() : go(i as Stage))}
                  >
                    <StepMark state={state} n={i + 1} />
                    {name}
                    <span className="sr-only"> ({STEP_STATE_LABEL[state]})</span>
                  </button>
                </Fragment>
              );
            })}
          </nav>
        </div>
      </header>

      <div className="app">
        <div className="page-head">
          <div className="page-title">
            <h1>
              <span className="page-num">{stage + 1}.</span> {STAGES[stage]}
            </h1>
            <p>
              {STAGE_DESCRIPTIONS[stage]}
              {stage === 1 && (
                <button className="info-btn" aria-label="분석 카드 읽는 법" onClick={() => setGuideOpen(true)}>
                  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                    <circle cx="6" cy="2.5" r="1.25" fill="currentColor" />
                    <rect x="5" y="5" width="2" height="6" rx="1" fill="currentColor" />
                  </svg>
                </button>
              )}
            </p>
          </div>
          {footer && <div className="page-actions">{footer}</div>}
        </div>

        <main className="main">
          {stage === 0 && (
            <div className="two-col equal">
              <section>
                <div className="editor">
                  <div className="editor-head">
                    <Select
                      id="language"
                      ariaLabel="코딩 언어"
                      value={language}
                      options={LANGUAGES.map((l) => ({ value: l.id, label: l.name, hint: l.version }))}
                      onChange={setLanguage}
                    />
                    <EditorMeta code={code} language={language_} />
                  </div>
                  <CodeEditor
                    code={code}
                    placeholder={"이해하고 싶은 코드를 입력해 주세요.\n코드는 실행하지 않고, 분석을 위해 OpenAI API로 전송됩니다."}
                    focus={focusLines}
                    warn={warnLines}
                    reveal={syntaxLine}
                    onChange={(next) => {
                      setCode(next);
                      setFocusLines([]);
                      if (syntaxLine) setError(null);
                    }}
                    onFocusChange={setFocusLines}
                  />
                </div>
                {overLimit && <p className="danger small">글자 수 한도를 넘었어요. 코드를 줄여 주세요.</p>}
              </section>
              <section className="fields">
                <div className="field grow">
                  <label htmlFor="question" className="field-title">
                    무엇이 궁금한가요?
                  </label>
                  <textarea
                    id="question"
                    className="plain"
                    spellCheck={false}
                    value={question}
                    placeholder={
                      "궁금한 내용을 번호로 구분해서 작성해 주세요.\n아무것도 입력하지 않으면 전체적인 코드의 흐름을 설명합니다."
                    }
                    onChange={(e) => setQuestion(e.target.value)}
                  />
                </div>
                <div className="field" role="radiogroup" aria-labelledby="level-title">
                  <div id="level-title" className="field-title">
                    설명 수준
                  </div>
                  <div>
                    {LEVELS.map(([value, name]) => (
                      <label key={value} className="radio">
                        <input type="radio" name="level" checked={level === value} onChange={() => setLevel(value)} />
                        {name}
                      </label>
                    ))}
                  </div>
                </div>
              </section>
            </div>
          )}

          {stage === 1 && analysis && (
            <div className="two-col equal">
              <section>
                <CodeView code={code} language={language_} focus={new Set(focusLines)} step={stepLines} />
              </section>
              <section className="steps-col">
                <div className="steps-scroll">
                  {focusSteps.length > 0 && (
                    <p className="banner info">
                      질문한 줄은 {focusSteps.map((s) => `${s.id}단계(${s.title})`).join(", ")}에 있어요.
                    </p>
                  )}
                  <ol className="steps">
                    {analysis.steps.map((s) => (
                      <li key={s.id}>
                        <StepCard
                          step={s}
                          selected={selectedStep === s.id}
                          onSelect={() => setSelectedStep(selectedStep === s.id ? null : s.id)}
                        />
                      </li>
                    ))}
                  </ol>
                </div>
              </section>
            </div>
          )}

          {noChallenge && (
            <section className="panel wide">
              <p className="banner info">
                {dps.length === 0
                  ? "이 코드에서는 설계 포인트를 찾지 못했어요. 코드 분석을 보거나 설계 응용으로 넘어가 보세요."
                  : "아직 선택한 설계 포인트가 없어요. 코드 분석에서 '설계 선택'을 눌러 시작하세요."}
              </p>
            </section>
          )}

          {stage === 2 && dp && (
            <div className="stack">
              <div className="two-col equal">
                <section>
                  <CodeView code={code} language={language_} focus={new Set()} step={new Set(dp.lines)} />
                </section>
                <section className="dp-col">
                  <div className="field dp-field">
                    <div className="field-title muted">
                      설계 포인트 {dpIndex + 1}/{dps.length} · line: {formatLines(dp.lines)}
                    </div>
                    <div className="question-row">
                      <h2>{dp.prompt}</h2>
                      <HintButton open={showHint} onToggle={() => setShowHint(!showHint)} />
                    </div>
                    <div className="options" role="radiogroup" aria-label="선택지">
                      {dp.options.map((o) => (
                        <label key={o.id} className={`card option ${choice === o.id ? "selected" : ""}`}>
                          <input type="radio" name="option" checked={choice === o.id} onChange={() => setChoice(o.id)} />
                          <span>
                            <strong>
                              {o.id}. {o.label}
                            </strong>
                            {o.detail && <span className="muted small option-detail">{o.detail}</span>}
                          </span>
                        </label>
                      ))}
                    </div>
                  </div>
                </section>
              </div>
              <div className="field reason">
                <label htmlFor="reason" className="field-title">
                  선택한 이유
                </label>
                <textarea
                  id="reason"
                  className="plain"
                  spellCheck={false}
                  value={reason}
                  placeholder="이 방식을 선택한 이유를 작성해 주세요. 어떤 상황을 고려했는지 작성하면 더 정확히 평가할 수 있어요."
                  onChange={(e) => setReason(e.target.value)}
                />
              </div>
            </div>
          )}

          {stage === 3 && dp && verdict && (
            <section className="panel">
              {!verdict.skipped && (
                <h2>
                  판단 기준 {verdict.total}개 중 {verdict.hit}개를 반영했어요
                </h2>
              )}
              <div className={`two-col compare${verdict.mine ? "" : " single"}`}>
                {verdict.mine && (
                  <section>
                    <h3>내가 선택한 답</h3>
                    <div className="card">
                      <strong>
                        {verdict.mine.choice}. {mineLabel}
                      </strong>
                      <p>{verdict.mine.reason || "-"}</p>
                    </div>
                  </section>
                )}
                <section>
                  <h3>원본 코드의 설계</h3>
                  <div className="card">
                    <strong>
                      {dp.codeOption}. {verdict.codeChoice.label}
                    </strong>
                    <p>{verdict.codeChoice.summary}</p>
                    <div className="evidence-list">
                      <EvidenceBadge evidence={verdict.codeChoice.intent} />
                      <EvidenceBadge evidence={verdict.tradeoffs.evidence} />
                    </div>
                  </div>
                </section>
              </div>
              <h3>판단 기준</h3>
              {verdict.conditions.map((c) => {
                const condition = dp.conditions.find((x) => x.id === c.id);
                return (
                  <div key={c.id} className={`card condition ${c.status ?? ""}`}>
                    <div className="row">
                      <strong>{condition?.label ?? c.id}</strong>
                      {c.status && <span className={`badge ${c.status}`}>{c.status === "hit" ? "반영" : "미반영"}</span>}
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
                      // 내가 고른 방식의 행만 옅게 칠한다(방식 이름은 서버가 선택지 이름으로 채운다).
                      <tr key={r.approach} className={r.approach === mineLabel ? "mine" : undefined}>
                        <td>{r.approach}</td>
                        <td>{r.waitTime}</td>
                        <td>{r.onFailure}</td>
                        <td>{r.complexity}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {stage === 4 && analysis && (
            <div className="stack">
              <section className="panel">
                <h2>어떤 조건을 추가할까요?</h2>
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
              </section>
              {/* 조건을 고르기 전에도 자리를 잡아 둬서, 고른 순간 아래 레이아웃이 튀지 않는다 */}
              <section className="panel apply">
                {preset && work ? (
                  <>
                    <p className="question">{work.scenario.question}</p>
                    <div className="question-row">
                      <p className="muted small">{work.scenario.followUp}</p>
                      <HintButton open={showHint} onToggle={() => setShowHint(!showHint)} />
                    </div>
                    <textarea
                      className="fixed"
                      aria-label="답"
                      rows={5}
                      value={work.answer}
                      placeholder="이 조건에서 설계가 어떻게 달라져야 할지 작성해 주세요."
                      onChange={(e) => updateWork(preset, { answer: e.target.value })}
                    />
                    <div className="apply-actions">
                      <button aria-pressed={work.showModel} disabled={loading !== null} onClick={toggleModelAnswer}>
                        {work.showModel ? "모범 답변 숨기기" : "모범 답변 확인하기"}
                      </button>
                      <button
                        className="primary"
                        disabled={!work.answer.trim() || loading !== null}
                        onClick={submitFeedback}
                      >
                        제출
                      </button>
                    </div>
                  </>
                ) : (
                  <p className="apply-empty muted">추가할 조건을 선택해 주세요.</p>
                )}
              </section>
              {/* 제출 결과와 모범 답변은 입력 칸 아래 별도 섹션에 모은다 */}
              {work && (work.feedback || work.showModel) && (
                <section className="panel result">
                  {work.feedback && (
                    <div className="feedback">
                      <strong className="feedback-verdict">{work.feedback.verdict}</strong>
                      {work.feedback.points.length > 0 && (
                        <div className="feedback-part">
                          <span className="badge hit">좋은 점</span>
                          <ul>
                            {work.feedback.points.map((p) => (
                              <li key={p}>{p}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <div className="feedback-part">
                        <span className="badge further">아쉬운 점</span>
                        <p>{work.feedback.further}</p>
                      </div>
                    </div>
                  )}
                  {work.showModel && (
                    <div className="feedback-part model-answer">
                      <span className="badge model">모범 답변</span>
                      <p>{work.modelAnswer}</p>
                    </div>
                  )}
                </section>
              )}
            </div>
          )}
          {stage === 5 && report && (
            // 받는 HTML 보고서를 그대로 미리 보여 준다(스크립트 없는 문서라 sandbox로 막아 둔다).
            <iframe className="report-preview" title="학습 결과 요약 미리보기" sandbox="" srcDoc={reportHtml} />
          )}
        </main>

      </div>

      <LoadingDialog label={loading} />

      <SecretsDialog
        open={secretsDialog}
        findings={findings}
        onCancel={() => setSecretsDialog(false)}
        onConfirm={redactAndAnalyze}
      />

      <RestartDialog open={restartDialog} onCancel={() => setRestartDialog(false)} onConfirm={restart} />

      <StepGuideDialog open={guideOpen} onClose={() => setGuideOpen(false)} />

      <div className="toasts">
        {error && (
          <Toast
            tone="error"
            label="오류"
            onClose={() => setError(null)}
            action={
              !["syntax_error", "input_invalid", "language_unsupported"].includes(error.error.code) && (
                <button onClick={error.retry}>다시 시도</button>
              )
            }
          >
            {/* 서버 메시지는 문장마다 줄을 바꿔 읽기 쉽게 보여 준다. */}
            <strong className="toast-title">{error.error.message.replace(/([.?!])\s+/g, "$1\n")}</strong>
            {syntaxLine && <span className="toast-sub">입력창에 표시한 {syntaxLine}번째 줄을 확인해 주세요.</span>}
          </Toast>
        )}
        {stage === 2 && dp && showHint && (
          <Toast tone="hint" label="힌트" onClose={() => setShowHint(false)}>
            {dp.hint}
          </Toast>
        )}
        {stage === 4 && work && showHint && (
          <Toast tone="hint" label="힌트" onClose={() => setShowHint(false)}>
            {work.scenario.hint}
          </Toast>
        )}
      </div>
    </>
  );
}

// 단계 표시 앞의 표시: 완료는 체크, 건너뜀은 건너뛰기 화살표, 그 밖에는 번호.
function StepMark({ state, n }: { state: StepState; n: number }) {
  if (state === "done") {
    return (
      <svg className="stepnav-mark" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
        <path d="M3 7.2 5.8 10 11 4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (state === "skipped") {
    return (
      <svg className="stepnav-mark" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
        <path d="M2.5 9.5a4.5 4.5 0 0 1 8.2-2.6M11 3.8v3.4H7.6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <span className="stepnav-mark num" aria-hidden="true">
      {n}
    </span>
  );
}

// 진행 중 모달. 요청이 끝날 때까지 배경 클릭·Esc로 닫히지 않는다.
// 작업 이름별 로딩 제목. 없으면 "{작업} 중이에요"로 쓴다.
const LOADING_TITLES: Record<string, string> = {
  분석: "코드를 분석하고 있어요",
  "질문 만들기": "질문을 생성하고 있어요",
  "모범 답변": "모범 답변을 생성하고 있어요",
  "학습 정리": "학습 내용을 정리하고 있어요",
};

function LoadingDialog({ label }: { label: string | null }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (label && !dialog.open) dialog.showModal();
    if (!label && dialog.open) dialog.close();
    if (!label) return;
    setSeconds(0);
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 500);
    return () => clearInterval(timer);
  }, [label]);

  const current = ANALYZE_STEPS.filter(([, at]) => seconds >= at).length - 1;
  return (
    <dialog ref={ref} className="dialog loading" aria-labelledby="loading-title" onCancel={(e) => e.preventDefault()}>
      <div className="dialog-body" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <h2 id="loading-title">{(label && LOADING_TITLES[label]) ?? `${label} 중이에요`}</h2>
        {label === "분석" ? (
          <>
            <ol className="progress">
              {ANALYZE_STEPS.map(([text], i) => (
                <li key={text} className={i < current ? "done" : i === current ? "current" : ""}>
                  <span className="progress-mark" aria-hidden="true" />
                  {text}
                </li>
              ))}
            </ol>
            <p className="faint small">약 30~40초 소요</p>
          </>
        ) : (
          <p className="muted small">잠시만 기다려 주세요.</p>
        )}
      </div>
    </dialog>
  );
}

type LanguageInfo = (typeof LANGUAGES)[number];

// 에디터 헤더 오른쪽 정보(언어 버전 · 줄 수 · 글자 수). 입력창과 읽기 전용 코드 뷰가 같이 쓴다.
function EditorMeta({ code, language }: { code: string; language: LanguageInfo }) {
  const lines = code ? code.split("\n").length : 0;
  return (
    <div className="editor-meta">
      <span>
        {language.name} {language.version}
      </span>
      <span>{lines.toLocaleString()} lines</span>
      <span className={code.length > MAX_CODE_CHARACTERS ? "danger" : ""}>
        {code.length.toLocaleString()} / {MAX_CODE_CHARACTERS.toLocaleString()}자
      </span>
    </div>
  );
}

// 읽기 전용 코드 뷰. 1단계 입력창과 같은 틀(헤더·줄 번호·높이 고정 스크롤)에 입력만 막았다.
// 강조할 단계 줄이 바뀌면 그 첫 줄이 보이도록 안에서 스크롤한다.
function CodeView(props: { code: string; language: LanguageInfo; focus: Set<number>; step: Set<number> }) {
  const lines = useMemo(() => highlightLines(props.code), [props.code]);
  const body = useRef<HTMLDivElement>(null);
  const firstStep = Math.min(...props.step);

  useEffect(() => {
    const target = body.current?.querySelector<HTMLElement>(".line.step");
    if (!body.current || !target) return;
    const { scrollTop, clientHeight } = body.current;
    const top = target.offsetTop;
    if (top < scrollTop || top > scrollTop + clientHeight - 40) {
      body.current.scrollTo({ top: Math.max(0, top - 48), behavior: "smooth" });
    }
  }, [firstStep]);

  return (
    <div className="editor readonly">
      <div className="editor-head">
        <span className="editor-lang">{props.language.name}</span>
        <EditorMeta code={props.code} language={props.language} />
      </div>
      <div className="editor-body" ref={body}>
        <div className="editor-grid">
          <div className="editor-gutter">
            {lines.map((_, i) => {
              const n = i + 1;
              const cls = [props.step.has(n) && "step", props.focus.has(n) && "focus"].filter(Boolean).join(" ");
              return (
                <span key={n} className={`gutter ${cls}`}>
                  {n}
                </span>
              );
            })}
          </div>
          <div className="editor-code">
            <pre className="editor-highlight">
              {lines.map((tokens, i) => {
                const n = i + 1;
                const cls = [props.step.has(n) && "step", props.focus.has(n) && "focus"].filter(Boolean).join(" ");
                return (
                  <div key={n} className={`line ${cls}`}>
                    {tokens.length === 0
                      ? " "
                      : tokens.map((t, j) =>
                          t.kind ? (
                            <span key={j} className={`tok-${t.kind}`}>
                              {t.text}
                            </span>
                          ) : (
                            t.text
                          ),
                        )}
                  </div>
                );
              })}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}

// 힌트 토스트를 여닫는 전구 버튼(설계 선택·설계 응용 공통).
function HintButton({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button className="hint-btn" aria-label="힌트 보기" aria-expanded={open} onClick={onToggle}>
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
        <path
          d="M8 1.5a4.5 4.5 0 0 0-2.7 8.1c.4.3.7.8.7 1.3V12h4v-1.1c0-.5.3-1 .7-1.3A4.5 4.5 0 0 0 8 1.5ZM6 13.5h4M6.8 15h2.4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}

// 화면 오른쪽 아래에 쌓이는 알림. 저절로 사라지지 않고 닫기를 눌러야 닫힌다(오류·힌트는 읽는 동안 남아야 한다).
function Toast(props: { tone: "error" | "hint"; label: string; onClose: () => void; action?: ReactNode; children: ReactNode }) {
  return (
    <div className={`toast ${props.tone}`} role={props.tone === "error" ? "alert" : "status"}>
      <span className="toast-label" aria-hidden="true">
        {props.label}
      </span>
      <div className="toast-body">{props.children}</div>
      {props.action}
      <button className="dialog-close" aria-label={`${props.label} 닫기`} onClick={props.onClose}>
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
      </button>
    </div>
  );
}

// 처음으로 돌아가기 확인 모달. 분석과 학습 결과가 모두 사라지므로 한 번 더 묻는다.
function RestartDialog(props: { open: boolean; onCancel: () => void; onConfirm: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (props.open && !dialog.open) dialog.showModal();
    if (!props.open && dialog.open) dialog.close();
  }, [props.open]);

  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby="restart-title"
      onCancel={(e) => {
        e.preventDefault();
        props.onCancel();
      }}
      onClick={(e) => e.target === ref.current && props.onCancel()}
    >
      <div className="dialog-body">
        <span className="dialog-icon" aria-hidden="true">
          !
        </span>
        <h2 id="restart-title">코드 입력으로 돌아갈까요?</h2>
        <p className="muted">
          지금까지 분석하고 학습한 내용이 모두 초기화되고,
          <br />
          첫 단계인 코드 입력으로 돌아가요.
        </p>
      </div>
      <div className="dialog-actions">
        <button autoFocus onClick={props.onCancel}>
          취소
        </button>
        <button className="primary" onClick={props.onConfirm}>
          초기화하고 이동
        </button>
      </div>
    </dialog>
  );
}

// 비밀값 확인 모달. <dialog>의 showModal로 띄워 포커스 가두기와 Esc 닫기를 브라우저에 맡긴다.
function SecretsDialog(props: {
  open: boolean;
  findings: SecretFinding[];
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (props.open && !dialog.open) dialog.showModal();
    if (!props.open && dialog.open) dialog.close();
  }, [props.open]);

  const lines = [...new Set(props.findings.map((f) => f.line))].sort((a, b) => a - b);
  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby="secrets-title"
      onCancel={(e) => {
        e.preventDefault();
        props.onCancel();
      }}
      onClick={(e) => e.target === ref.current && props.onCancel()}
    >
      <div className="dialog-body">
        <span className="dialog-icon" aria-hidden="true">
          !
        </span>
        <h2 id="secrets-title">{lines.join(", ")}번째 줄에 API 키로 보이는 값이 있어요</h2>
        <p className="muted">
          분석을 시작하면 코드가 외부 API로 전송돼요.
          <br />
          아래 값을 가린 뒤 진행할까요?
        </p>
        <ul className="dialog-list">
          {props.findings.map((f, i) => (
            <li key={i}>
              <code>{f.preview}</code> <span className="faint">{f.kind}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="dialog-actions">
        <button onClick={props.onCancel}>취소</button>
        <button className="primary" autoFocus onClick={props.onConfirm}>
          진행하기
        </button>
      </div>
    </dialog>
  );
}

// IDE 스타일 입력창. 하이라이트한 <pre> 위에 글자가 투명한 textarea를 겹쳐, 입력은 textarea가 받고
// 색은 <pre>가 보여 준다. 줄 번호를 클릭하거나 드래그하면 궁금한 줄이 선택·해제된다.
function CodeEditor(props: {
  code: string;
  placeholder: string;
  focus: number[];
  warn: Set<number>;
  /** 이 줄이 보이도록 입력창을 스크롤한다(문법 오류 줄). */
  reveal: number | null;
  onChange: (code: string) => void;
  onFocusChange: (lines: number[]) => void;
}) {
  const lines = useMemo(() => highlightLines(props.code), [props.code]);
  const focus = new Set(props.focus);
  const drag = useRef<{ anchor: number; base: Set<number>; adding: boolean } | null>(null);
  // Esc를 누른 직후의 Tab은 들여쓰기 대신 다음 요소로 포커스를 넘긴다(키보드 사용자가 갇히지 않게).
  const tabReleased = useRef(false);
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const target = props.reveal ? body.current?.querySelectorAll<HTMLElement>(".gutter")[props.reveal - 1] : null;
    if (!body.current || !target) return;
    const offset = target.getBoundingClientRect().top - body.current.getBoundingClientRect().top + body.current.scrollTop;
    body.current.scrollTo({ top: Math.max(0, offset - body.current.clientHeight / 3), behavior: "smooth" });
  }, [props.reveal]);

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const text = e.clipboardData.getData("text/plain");
    if (!text) return;
    e.preventDefault();
    const ta = e.currentTarget;
    const before = ta.value.slice(0, ta.selectionStart);
    const lineStart = before.slice(before.lastIndexOf("\n") + 1);
    // 커서 앞이 공백뿐이면 그 들여쓰기에 맞춘다. 줄 중간이면 붙여넣은 모양 그대로 둔다.
    const base = /^ *$/.test(lineStart) ? lineStart : "";
    replaceRange(ta, ta.selectionStart, ta.selectionEnd, reindent(formatPasted(text), base));
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const ta = e.currentTarget;
    const { value, selectionStart: start, selectionEnd: end } = ta;
    if (e.key === "Escape") {
      tabReleased.current = true;
      return;
    }
    if (e.key === "Tab" && tabReleased.current) {
      tabReleased.current = false;
      return;
    }
    tabReleased.current = false;
    if (e.nativeEvent.isComposing || e.metaKey || e.ctrlKey || e.altKey) return;

    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    if (e.key === "Tab") {
      e.preventDefault();
      const multiLine = value.slice(start, end).includes("\n");
      if (!e.shiftKey && !multiLine) {
        const column = start - lineStart;
        replaceRange(ta, start, end, " ".repeat(INDENT.length - (column % INDENT.length)));
        return;
      }
      // 여러 줄 선택이거나 Shift+Tab이면 걸친 줄 전체를 한 단계 들여쓰거나 내어쓴다.
      const blockEnd = value.indexOf("\n", end - (end > start && value[end - 1] === "\n" ? 1 : 0));
      const stop = blockEnd === -1 ? value.length : blockEnd;
      const lines = value.slice(lineStart, stop).split("\n");
      const changed = lines.map((line) =>
        e.shiftKey ? line.slice(Math.min(leadingSpaces(line).length, INDENT.length)) : line ? INDENT + line : line,
      );
      const firstDelta = changed[0].length - lines[0].length;
      replaceRange(ta, lineStart, stop, changed.join("\n"));
      const newStart = Math.max(lineStart, start + firstDelta);
      ta.setSelectionRange(multiLine ? lineStart : newStart, multiLine ? lineStart + changed.join("\n").length : newStart);
      return;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      replaceRange(ta, start, end, "\n" + nextIndent(value.slice(lineStart, start)));
      return;
    }
    if (e.key === "Backspace" && start === end && start > lineStart) {
      // 커서 앞이 공백뿐이면 들여쓰기 한 단계(4칸)씩 지운다.
      const spaces = value.slice(lineStart, start);
      if (/^ +$/.test(spaces)) {
        e.preventDefault();
        const remove = spaces.length % INDENT.length || INDENT.length;
        replaceRange(ta, start - remove, start, "");
      }
    }
  };

  useEffect(() => {
    const end = () => (drag.current = null);
    window.addEventListener("mouseup", end);
    return () => window.removeEventListener("mouseup", end);
  }, []);

  // 드래그 시작 줄부터 지금 줄까지를, 시작 줄의 상태에 따라 한꺼번에 선택하거나 해제한다.
  const dragTo = (to: number) => {
    const d = drag.current;
    if (!d) return;
    const next = new Set(d.base);
    for (let n = Math.min(d.anchor, to); n <= Math.max(d.anchor, to); n++) {
      if (d.adding) next.add(n);
      else next.delete(n);
    }
    props.onFocusChange([...next].sort((a, b) => a - b));
  };

  return (
    <div className="editor-body" ref={body}>
      <div className="editor-grid">
        <div className="editor-gutter">
          {lines.map((_, i) => {
            const n = i + 1;
            const cls = [focus.has(n) && "focus", props.warn.has(n) && "warn"].filter(Boolean).join(" ");
            return (
              <button
                key={n}
                type="button"
                className={`gutter ${cls}`}
                disabled={!props.code}
                aria-pressed={focus.has(n)}
                aria-label={`${n}번째 줄 선택`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  drag.current = { anchor: n, base: new Set(props.focus), adding: !focus.has(n) };
                  dragTo(n);
                }}
                onMouseEnter={() => dragTo(n)}
                onClick={(e) => {
                  // 마우스는 mousedown에서 처리했으니 키보드(Enter·Space)일 때만 토글한다.
                  if (e.detail !== 0) return;
                  props.onFocusChange(
                    focus.has(n) ? props.focus.filter((x) => x !== n) : [...props.focus, n].sort((a, b) => a - b),
                  );
                }}
              >
                {n}
              </button>
            );
          })}
        </div>
        <div className="editor-code">
          <pre className="editor-highlight" aria-hidden="true">
            {lines.map((tokens, i) => {
              const n = i + 1;
              const cls = [focus.has(n) && "focus", props.warn.has(n) && "warn"].filter(Boolean).join(" ");
              return (
                <div key={n} className={`line ${cls}`}>
                  {tokens.length === 0
                    ? " "
                    : tokens.map((t, j) =>
                        t.kind ? (
                          <span key={j} className={`tok-${t.kind}`}>
                            {t.text}
                          </span>
                        ) : (
                          t.text
                        ),
                      )}
                </div>
              );
            })}
          </pre>
          <textarea
            id="code"
            className="editor-input"
            aria-label="코드"
            value={props.code}
            placeholder={props.placeholder}
            wrap="off"
            spellCheck={false}
            autoCapitalize="off"
            autoComplete="off"
            autoCorrect="off"
            onChange={(e) => props.onChange(e.target.value)}
            onPaste={onPaste}
            onKeyDown={onKeyDown}
          />
        </div>
      </div>
    </div>
  );
}

// 실행 취소(Cmd+Z)가 살아 있도록 execCommand로 바꾸고, 안 되는 브라우저에서는 setRangeText로 바꾼다.
// 어느 쪽이든 input 이벤트가 나가 React onChange로 상태가 갱신된다.
function replaceRange(ta: HTMLTextAreaElement, start: number, end: number, text: string) {
  ta.focus();
  ta.setSelectionRange(start, end);
  const done = text ? document.execCommand("insertText", false, text) : document.execCommand("delete");
  if (!done) {
    ta.setRangeText(text, start, end, "end");
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

// 화면용 가벼운 Python 하이라이트. 정확한 파싱은 서버(AST)가 하므로 여기서는 색만 입힌다.
type Token = { text: string; kind?: "keyword" | "string" | "comment" | "number" | "builtin" | "decorator" };

const KEYWORDS = new Set(
  ("False None True and as assert async await break class continue def del elif else except finally for from " +
    "global if import in is lambda nonlocal not or pass raise return try while with yield match case self").split(" "),
);
const BUILTINS = new Set(
  ("print len range dict list set tuple int str float bool bytes open isinstance super enumerate zip map filter " +
    "sorted min max sum any all type iter next getattr setattr hasattr Exception ValueError KeyError TypeError " +
    "RuntimeError").split(" "),
);
const TOKEN_RE =
  /(#[^\n]*)|([rRbBuUfF]{0,2}(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?))|(@[A-Za-z_][\w.]*)|(\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|([A-Za-z_]\w*)|([^\w#"'@]+|[\s\S])/g;

function highlightLines(code: string): Token[][] {
  const lines: Token[][] = [[]];
  for (const m of code.matchAll(TOKEN_RE)) {
    const [text, comment, string, decorator, number, word] = m;
    const kind: Token["kind"] = comment
      ? "comment"
      : string
        ? "string"
        : decorator
          ? "decorator"
          : number
            ? "number"
            : word && KEYWORDS.has(word)
              ? "keyword"
              : word && BUILTINS.has(word)
                ? "builtin"
                : undefined;
    // 여러 줄 문자열도 줄 단위로 끊어 줄 번호와 맞춘다.
    text.split("\n").forEach((part, i) => {
      if (i > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ text: part, kind });
    });
  }
  return lines;
}

// 줄 번호 목록을 범위로 줄인다: [5, 6, 7, 9] → "5-7, 9"
// 코드 분석 단계 카드. 카드 사이 세로선이 번호를 이어 실행 순서를 보여 준다.
// 카드 어디를 눌러도 선택되고, 키보드는 제목 버튼으로 같은 동작을 한다(근거의 문서 링크와 겹치지 않게).
function StepCard({ step, selected, onSelect }: { step: Step; selected: boolean; onSelect: () => void }) {
  const cls = ["step-card", step.isDecision && "decision", selected && "selected"].filter(Boolean).join(" ");
  // 제목이 말줄임됐을 때만 전체 제목 툴팁을 띄운다.
  const [tip, setTip] = useState(false);
  const showTip = (e: { currentTarget: HTMLElement }) =>
    setTip(e.currentTarget.scrollWidth > e.currentTarget.clientWidth);
  return (
    <div className={cls} onClick={onSelect}>
      <span className="step-num" aria-hidden="true">
        {step.id}
      </span>
      <div className="step-body">
        <div className="step-head">
          <button
            className="step-title"
            aria-pressed={selected}
            onMouseEnter={showTip}
            onMouseLeave={() => setTip(false)}
            onFocus={showTip}
            onBlur={() => setTip(false)}
          >
            {step.title}
          </button>
          {tip && (
            <span className="tooltip" aria-hidden="true">
              {step.title}
            </span>
          )}
          {step.isDecision && <span className="badge decision-point">설계 포인트</span>}
        </div>
        <div className="step-meta">
          <code className="step-symbol">{step.symbol}</code>
          <span className="step-lines">line: {formatLines(step.lines)}</span>
        </div>
        <p className="step-summary">{step.summary}</p>
        {step.io && (
          <p className="step-io">
            입력: {step.io.in} · 출력: {step.io.out}
          </p>
        )}
        {step.evidence && step.evidence.length > 0 && (
          <div className="step-evidence">
            {step.evidence.map((e, i) => (
              <EvidenceBadge key={i} evidence={e} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const BADGE_LABEL: Record<Evidence["badge"], string> = {
  official: "공식 문서 확인",
  inferred: "추정",
  needs_check: "확인 필요",
};

// 문서 링크 문구: 출처 이름 · 심볼 (예: "Python 문서 · asyncio.gather")
function docsLabel(url: string): string {
  try {
    const host = new URL(url).hostname;
    return host === "docs.python.org" ? "Python 문서" : `${host.replace(/^www\./, "")} 문서`;
  } catch {
    return "공식 문서";
  }
}

function EvidenceBadge({ evidence }: { evidence: Evidence }) {
  return (
    <span className={`evidence ${evidence.badge}`}>
      <span className="badge">{BADGE_LABEL[evidence.badge]}</span>
      {evidence.url ? (
        <a href={evidence.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
          {docsLabel(evidence.url)}
          {evidence.symbol && ` · ${evidence.symbol}`}
        </a>
      ) : (
        evidence.symbol && <code>{evidence.symbol}</code>
      )}
      {evidence.note && <span className="evidence-note">{evidence.note}</span>}
    </span>
  );
}

// 분석 카드 읽는 법. 예시 카드 한 장을 두고 부분을 하나씩 짚어 가는 투어다.
// 짚는 부분은 data-part로 넘기고 강조는 CSS가 맡아서, StepCard는 그대로 재사용한다.
const GUIDE_SAMPLE: Step = {
  id: 1,
  title: "두 작업을 동시에 실행하기",
  symbol: "asyncio.gather",
  lines: [5, 6, 7],
  summary: "두 개의 asyncio.sleep 코루틴을 동시에 실행하고, 둘 다 끝날 때까지 기다린다.",
  io: { in: "asyncio.sleep(1, 'a'), asyncio.sleep(2, 'b')", out: "('a', 'b')" },
  isDecision: true,
  evidence: [
    {
      badge: "official",
      symbol: "asyncio.gather",
      url: "https://docs.python.org/3/library/asyncio-task.html#asyncio.gather",
      note: "await 가능한 객체들을 동시에 실행하고 결과를 순서대로 모아 반환한다.",
    },
  ],
};

const GUIDE_PARTS = [
  {
    part: "num",
    title: "실행 순서",
    body: "코드가 실행되는 순서예요. 카드 사이의 세로선이 번호를 이어 줘요. 설계 포인트인 단계는 노란색으로 표시돼요.",
  },
  {
    part: "title",
    title: "단계 제목",
    body: "이 단계가 하는 일을 한 줄로 요약했어요. 카드를 누르면 왼쪽 코드에서 해당 줄이 강조돼요.",
  },
  {
    part: "symbol",
    title: "핵심 심볼",
    body: "이 단계에서 중심이 되는 함수, 클래스, 문법의 이름이에요. 모르는 이름이라면 이것부터 찾아보면 돼요.",
  },
  {
    part: "lines",
    title: "코드 위치",
    body: "이 단계에 해당하는 코드 줄이에요. line: 5는 5번째 줄, line: 5-7은 5번째부터 7번째 줄까지를 뜻해요.",
  },
  {
    part: "decision",
    title: "설계 포인트",
    body: "다른 방식으로도 짤 수 있었던 선택이 들어 있는 단계예요. '설계 선택' 단계에서 나라면 어떻게 짰을지 직접 골라 볼 수 있어요.",
  },
  {
    part: "summary",
    title: "설명",
    body: "이 단계가 무엇을 하는지 풀어 쓴 설명이에요. 선택한 설명 수준에 따라 자세한 정도가 달라져요.",
  },
  {
    part: "io",
    title: "입력과 출력",
    body: "이 단계에 들어가는 값과 그 결과로 나오는 값이에요. 값이 오가지 않는 단계에는 표시되지 않아요.",
  },
  {
    part: "evidence",
    title: "근거",
    body: "설명의 출처와 확실한 정도예요. 링크를 누르면 해당 문서가 열려요.",
  },
] as const;

// 근거 단계에서 배지 세 종류를 실제 배지 모양으로 보여 준다.
const BADGE_GUIDE: Record<Evidence["badge"], string> = {
  official: "공식 문서에서 확인한 내용이에요.",
  inferred: "문서 없이 코드만 보고 추론한 내용이에요.",
  needs_check: "근거를 찾지 못했어요. 직접 확인해 보세요.",
};

function StepGuideDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const sampleRef = useRef<HTMLDivElement>(null);
  const [index, setIndex] = useState(0);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setIndex(0);
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  // 예시 카드는 보여 주기만 한다. React 18은 inert를 속성으로 받지 않아 직접 단다.
  useEffect(() => {
    sampleRef.current?.setAttribute("inert", "");
  }, []);

  const current = GUIDE_PARTS[index];
  const last = index === GUIDE_PARTS.length - 1;
  return (
    <dialog
      ref={ref}
      className="dialog guide"
      aria-labelledby="guide-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => e.target === ref.current && onClose()}
    >
      <div className="dialog-body">
        <h2>분석 카드 읽는 법</h2>
        <button className="dialog-close" aria-label="닫기" onClick={onClose}>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
        </button>
        <div ref={sampleRef} className="guide-sample" data-part={current.part}>
          <StepCard step={GUIDE_SAMPLE} selected={false} onSelect={() => {}} />
        </div>
        <div className="guide-text" aria-live="polite">
          <span className="guide-count">
            {index + 1} / {GUIDE_PARTS.length}
          </span>
          <h3 id="guide-title">{current.title}</h3>
          <p>{current.body}</p>
          {current.part === "evidence" && (
            <ul className="guide-legend">
              {(Object.keys(BADGE_GUIDE) as Evidence["badge"][]).map((b) => (
                <li key={b} className={`evidence ${b}`}>
                  <span className="badge">{BADGE_LABEL[b]}</span>
                  {BADGE_GUIDE[b]}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <div className="dialog-actions">
        <button onClick={() => setIndex(index - 1)} disabled={index === 0}>
          이전
        </button>
        <button className="primary" autoFocus onClick={() => (last ? onClose() : setIndex(index + 1))}>
          {last ? "확인" : "다음"}
        </button>
      </div>
    </dialog>
  );
}
