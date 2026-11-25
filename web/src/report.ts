// 6단계 학습 결과 요약 문서. 1~5단계 기록은 화면 상태 그대로 조립하고, LLM은 핵심 정리·다시 볼 것만 쓴다.
// 같은 데이터로 Markdown과 단독 HTML 보고서(서비스 다크 톤)를 만든다.
import type {
  AnalyzeResponse,
  Constraint,
  DecisionPoint,
  FeedbackResponse,
  ScenarioResponse,
  SummaryResponse,
  Verdict,
} from "./api/client";

export type ReportApplication = {
  constraint: Constraint;
  scenario: ScenarioResponse;
  answer: string;
  feedback: FeedbackResponse | null;
  modelAnswer: string | null;
};

export type ReportData = {
  createdAt: Date;
  languageName: string;
  code: string;
  analysis: AnalyzeResponse;
  decisions: { dp: DecisionPoint; verdict: Verdict }[];
  applications: ReportApplication[];
  summary: SummaryResponse;
};

export function formatLines(lines: number[]): string {
  const sorted = [...new Set(lines)].sort((a, b) => a - b);
  const ranges: string[] = [];
  for (let i = 0; i < sorted.length; i++) {
    const start = sorted[i];
    while (sorted[i + 1] === sorted[i] + 1) i++;
    ranges.push(start === sorted[i] ? `${start}` : `${start}-${sorted[i]}`);
  }
  return ranges.join(", ");
}

function formatDate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function reportFileName(data: ReportData, ext: "md" | "html"): string {
  return `ai-code-tutor-${formatDate(data.createdAt).replace(/[-: ]/g, "").slice(0, 12)}.${ext}`;
}

// 핵심 정리의 ref("step:2", "decision:dp1", "apply:k1")를 사람이 읽는 이름으로 바꾼다.
function refLabel(data: ReportData, ref: string): string {
  const [kind, id] = ref.split(":");
  if (kind === "step") {
    const step = data.analysis.steps.find((s) => String(s.id) === id);
    return step ? `코드 분석 ${step.id}단계` : ref;
  }
  if (kind === "decision") {
    const index = data.decisions.findIndex((d) => d.dp.id === id);
    return index >= 0 ? `설계 포인트 ${index + 1}` : ref;
  }
  const app = data.applications.find((a) => a.constraint.id === id);
  return app ? `설계 응용 · ${app.constraint.label}` : ref;
}

const optionLabel = (dp: DecisionPoint, id: string) => dp.options.find((o) => o.id === id)?.label ?? id;
const conditionLabel = (dp: DecisionPoint, id: string) => dp.conditions.find((c) => c.id === id)?.label ?? id;
const STATUS_LABEL = { hit: "반영", miss: "미반영" } as const;

// --- Markdown -----------------------------------------------------------

// 표 칸 안의 |와 줄바꿈이 표를 깨지 않게 한다.
const cell = (text: string) => text.replace(/\|/g, "\\|").replace(/\n+/g, " ");
// 코드 안에 ```가 있어도 닫히지 않게 더 긴 울타리를 쓴다.
const fence = (code: string) => "`".repeat(Math.max(3, ...[...code.matchAll(/`+/g)].map((m) => m[0].length + 1)));

export function buildMarkdown(data: ReportData): string {
  const out: string[] = [
    "# 학습 결과 요약",
    "",
    `작성: ${formatDate(data.createdAt)}`,
    "",
    "## 핵심 정리",
    "",
    ...data.summary.takeaways.map(
      (t, i) => `${i + 1}. ${t.text}  \n   <sub>근거: ${t.refs.map((r) => refLabel(data, r)).join(", ")}</sub>`,
    ),
  ];
  if (data.summary.reviewPoints.length) {
    out.push("", "## 다시 볼 것", "", ...data.summary.reviewPoints.map((p) => `- ${p}`));
  }

  out.push("", "## 1. 코드 분석", "");
  for (const step of data.analysis.steps) {
    out.push(`### ${step.id}단계. ${step.title}`, "", `\`${step.symbol}\` · line: ${formatLines(step.lines)}`, "", step.summary);
    if (step.io) out.push("", `- 입력: ${step.io.in}`, `- 출력: ${step.io.out}`);
    out.push("");
  }

  if (data.decisions.length) {
    out.push("## 2. 설계 선택 · 비교", "");
    data.decisions.forEach(({ dp, verdict }, i) => {
      out.push(`### 설계 포인트 ${i + 1}. ${dp.prompt}`, "", `line: ${formatLines(dp.lines)}`, "");
      out.push(
        verdict.mine
          ? `- **내가 선택한 답:** ${optionLabel(dp, verdict.mine.choice)}${verdict.mine.reason.trim() ? ` — ${verdict.mine.reason.trim()}` : ""}`
          : "- **내가 선택한 답:** 건너뜀(설계 비교만 봄)",
        `- **원본 코드의 설계:** ${verdict.codeChoice.label} — ${verdict.codeChoice.summary}`,
        "",
        "| 판단 기준 | 결과 | 설명 |",
        "| --- | --- | --- |",
        ...verdict.conditions.map(
          (c) => `| ${cell(conditionLabel(dp, c.id))} | ${c.status ? STATUS_LABEL[c.status] : "-"} | ${cell(c.note)} |`,
        ),
        "",
        "| 방식 | 기다리는 시간 | 실패 시 동작 | 복잡도 |",
        "| --- | --- | --- | --- |",
        ...verdict.tradeoffs.rows.map(
          (r) =>
            `| ${cell(r.approach)}${r.isThisCode ? " (원본)" : ""} | ${cell(r.waitTime)} | ${cell(r.onFailure)} | ${cell(r.complexity)} |`,
        ),
        "",
      );
    });
  }

  if (data.applications.length) {
    out.push(`## ${data.decisions.length ? 3 : 2}. 설계 응용`, "");
    for (const app of data.applications) {
      out.push(`### ${app.constraint.label}`, "", `> ${app.constraint.situation}`, "", `**질문:** ${app.scenario.question}`, "");
      out.push(`**내 답:** ${app.answer.trim() || "작성하지 않음"}`, "");
      if (app.feedback) {
        out.push(`**피드백:** ${app.feedback.verdict}`, "");
        if (app.feedback.points.length) out.push("- 좋은 점", ...app.feedback.points.map((p) => `  - ${p}`));
        out.push(`- 아쉬운 점: ${app.feedback.further}`, "");
      }
      if (app.modelAnswer) out.push(`**모범 답변:** ${app.modelAnswer}`, "");
    }
  }

  const f = fence(data.code);
  out.push("## 부록. 분석한 코드", "", `${f}${data.languageName.toLowerCase()}`, data.code.replace(/\n$/, ""), f, "");
  return out.join("\n");
}

// --- HTML ---------------------------------------------------------------

// 사용자 코드·답과 LLM 출력이 그대로 들어가므로 모든 텍스트를 이스케이프한다.
const esc = (text: string) =>
  text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

export function buildHtml(data: ReportData): string {
  const section = (n: number, title: string, body: string) =>
    `<section class="section"><h2><span class="num">${String(n).padStart(2, "0")}</span>${esc(title)}</h2>${body}</section>`;
  let n = 0;
  const parts: string[] = [];

  const takeaways = data.summary.takeaways
    .map(
      (t, i) =>
        `<li><span class="idx">${i + 1}</span><div><p>${esc(t.text)}</p><div class="refs">${t.refs
          .map((r) => `<span class="ref">${esc(refLabel(data, r))}</span>`)
          .join("")}</div></div></li>`,
    )
    .join("");
  const review = data.summary.reviewPoints.length
    ? `<div class="review"><span class="badge amber">다시 볼 것</span><ul>${data.summary.reviewPoints
        .map((p) => `<li>${esc(p)}</li>`)
        .join("")}</ul></div>`
    : "";
  parts.push(`<section class="summary"><h2>핵심 정리</h2><ol class="takeaways">${takeaways}</ol>${review}</section>`);

  parts.push(
    section(
      ++n,
      "코드 분석",
      `<ol class="steps">${data.analysis.steps
        .map(
          (step) => `<li class="card step">
  <div class="card-head"><span class="step-no">${step.id}</span><strong>${esc(step.title)}</strong>${step.isDecision ? '<span class="badge amber">설계 포인트</span>' : ""}</div>
  <div class="meta"><code>${esc(step.symbol)}</code><span>line: ${formatLines(step.lines)}</span></div>
  <p>${esc(step.summary)}</p>
  ${step.io ? `<dl class="io"><dt>입력</dt><dd>${esc(step.io.in)}</dd><dt>출력</dt><dd>${esc(step.io.out)}</dd></dl>` : ""}
</li>`,
        )
        .join("")}</ol>`,
    ),
  );

  if (data.decisions.length) {
    parts.push(
      section(
        ++n,
        "설계 선택 · 비교",
        data.decisions
          .map(({ dp, verdict }, i) => {
            const mine = verdict.mine
              ? `<strong>${esc(optionLabel(dp, verdict.mine.choice))}</strong>${verdict.mine.reason.trim() ? `<p>${esc(verdict.mine.reason.trim())}</p>` : ""}`
              : '<span class="muted">건너뜀 (설계 비교만 봄)</span>';
            const score = verdict.skipped ? "" : `<span class="badge accent score">판단 기준 ${verdict.hit}/${verdict.total} 반영</span>`;
            return `<article class="card decision">
  <div class="card-head"><span class="badge amber">설계 포인트 ${i + 1}</span><span class="muted">line: ${formatLines(dp.lines)}</span>${score}</div>
  <h3>${esc(dp.prompt)}</h3>
  <div class="compare">
    <div><span class="label">내가 선택한 답</span>${mine}</div>
    <div><span class="label">원본 코드의 설계</span><strong>${esc(verdict.codeChoice.label)}</strong><p>${esc(verdict.codeChoice.summary)}</p></div>
  </div>
  <ul class="conditions">${verdict.conditions
    .map(
      (c) =>
        `<li><span class="badge ${c.status ?? "plain"}">${c.status ? STATUS_LABEL[c.status] : "기준"}</span><div><strong>${esc(conditionLabel(dp, c.id))}</strong><p>${esc(c.note)}</p></div></li>`,
    )
    .join("")}</ul>
  <div class="table-wrap"><table><thead><tr><th>방식</th><th>기다리는 시간</th><th>실패 시 동작</th><th>복잡도</th></tr></thead><tbody>${verdict.tradeoffs.rows
    .map(
      (r) =>
        `<tr${r.isThisCode ? ' class="this"' : ""}><td>${esc(r.approach)}${r.isThisCode ? ' <span class="badge accent">원본</span>' : ""}</td><td>${esc(r.waitTime)}</td><td>${esc(r.onFailure)}</td><td>${esc(r.complexity)}</td></tr>`,
    )
    .join("")}</tbody></table></div>
</article>`;
          })
          .join(""),
      ),
    );
  }

  if (data.applications.length) {
    parts.push(
      section(
        ++n,
        "설계 응용",
        data.applications
          .map((app) => {
            const feedback = app.feedback
              ? `<div class="block"><span class="label">피드백</span><p class="verdict">${esc(app.feedback.verdict)}</p>${
                  app.feedback.points.length
                    ? `<div class="part"><span class="badge hit">좋은 점</span><ul>${app.feedback.points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>`
                    : ""
                }<div class="part"><span class="badge amber">아쉬운 점</span><p>${esc(app.feedback.further)}</p></div></div>`
              : "";
            const model = app.modelAnswer
              ? `<div class="block"><span class="label">모범 답변</span><p>${esc(app.modelAnswer)}</p></div>`
              : "";
            return `<article class="card apply">
  <div class="card-head"><span class="badge accent">${esc(app.constraint.label)}</span></div>
  <p class="muted">${esc(app.constraint.situation)}</p>
  <h3>${esc(app.scenario.question)}</h3>
  <div class="block"><span class="label">내 답</span><p>${app.answer.trim() ? esc(app.answer.trim()) : '<span class="muted">작성하지 않음</span>'}</p></div>
  ${feedback}${model}
</article>`;
          })
          .join(""),
      ),
    );
  }

  const codeRows = data.code
    .replace(/\n$/, "")
    .split("\n")
    .map((line, i) => `<tr><td class="ln">${i + 1}</td><td>${esc(line) || " "}</td></tr>`)
    .join("");
  parts.push(section(++n, "부록 · 분석한 코드", `<div class="code"><table>${codeRows}</table></div>`));

  return `<!doctype html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>학습 결과 요약 · ${esc(formatDate(data.createdAt))}</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap" />
<style>${REPORT_CSS}</style>
</head>
<body>
<main class="report">
  <header class="cover">
    <div class="brand"><span class="brand-mark">&lt;/&gt;</span><span>AI Code Tutor</span></div>
    <div class="title-row"><h1>학습 결과 요약</h1><time>${esc(formatDate(data.createdAt))}</time></div>
  </header>
  ${parts.join("\n")}
  <footer class="foot">AI Code Tutor로 만든 학습 정리예요. 핵심 정리는 AI가 학습 기록을 바탕으로 썼어요.</footer>
</main>
</body>
</html>
`;
}

// 서비스(styles.css)와 같은 색 토큰. 인쇄할 때는 밝은 종이 톤으로 바꾼다.
const REPORT_CSS = `
:root {
  color-scheme: dark;
  --bg: #0d1015; --surface: #141920; --surface-2: #1a2029; --editor: #0a0c10;
  --line: #252d38; --line-strong: #344050;
  --text: #d8dee7; --muted: #8a95a5; --faint: #5c6675;
  --accent: #5cb3ff; --accent-soft: rgba(92,179,255,.12);
  --green: #4fd18b; --green-soft: rgba(79,209,139,.12);
  --amber: #f0b54a; --amber-soft: rgba(240,181,74,.12);
  --red: #ff6b6b; --red-soft: rgba(255,107,107,.12);
  --sans: "IBM Plex Sans KR", system-ui, -apple-system, "Apple SD Gothic Neo", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.7 var(--sans); }
code { font-family: var(--mono); font-size: .9em; }
p { margin: 0; }
.report { max-width: 880px; margin: 0 auto; padding: 48px 20px 72px; display: flex; flex-direction: column; gap: 40px; }
.muted { color: var(--muted); }

.cover { padding-bottom: 28px; border-bottom: 1px solid var(--line); }
.brand { display: flex; align-items: center; gap: 10px; color: var(--muted); font-weight: 600; font-size: 14px; }
.brand-mark { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 7px; background: var(--surface-2);
  border: 1px solid var(--line-strong); color: var(--green); font: 600 11px var(--mono); }
.title-row { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-top: 18px; }
.title-row h1 { margin: 0; font-size: 30px; letter-spacing: -.02em; line-height: 1.3; }
.title-row time { color: var(--muted); font-size: 14px; }

h2 { display: flex; align-items: baseline; gap: 10px; margin: 0 0 16px; font-size: 20px; letter-spacing: -.01em; }
h2 .num { color: var(--accent); font: 500 14px var(--mono); }
h3 { margin: 0; font-size: 16px; line-height: 1.5; }
.section { display: flex; flex-direction: column; }

.summary { background: var(--surface); border: 1px solid var(--line); border-left: 3px solid var(--accent); border-radius: 12px; padding: 24px 26px; }
.takeaways { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }
.takeaways li { display: flex; gap: 12px; }
.takeaways .idx { flex: none; display: grid; place-items: center; width: 24px; height: 24px; margin-top: 2px; border-radius: 50%;
  background: var(--accent-soft); color: var(--accent); font: 600 12px var(--mono); }
.refs { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.ref { font-size: 12px; color: var(--muted); border: 1px solid var(--line-strong); border-radius: 999px; padding: 0 8px; }
.review { margin-top: 20px; padding-top: 18px; border-top: 1px solid var(--line); display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
.review ul { margin: 0; padding-left: 20px; }

.card { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; }
.section > .card + .card { margin-top: 14px; }
.card-head { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.card-head .score { margin-left: auto; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 6px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.badge.accent { background: var(--accent-soft); color: var(--accent); }
.badge.amber { background: var(--amber-soft); color: var(--amber); }
.badge.hit { background: var(--green-soft); color: var(--green); }
.badge.miss { background: var(--red-soft); color: var(--red); }
.badge.plain { background: var(--surface-2); color: var(--muted); }
.label { display: block; margin-bottom: 4px; color: var(--faint); font-size: 12px; font-weight: 600; }

.steps { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.step { gap: 8px; }
.step-no { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 50%; border: 1px solid var(--line-strong); color: var(--muted); font: 600 12px var(--mono); }
.meta { display: flex; flex-wrap: wrap; gap: 12px; color: var(--muted); font-size: 13px; }
.meta code { color: var(--accent); }
.io { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 0; font-size: 14px; }
.io dt { color: var(--faint); }
.io dd { margin: 0; }

.compare { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--line); border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.compare > div { background: var(--surface-2); padding: 12px 14px; }
.compare p { color: var(--muted); font-size: 14px; margin-top: 4px; }
.conditions { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.conditions li { display: flex; gap: 10px; align-items: flex-start; }
.conditions .badge { margin-top: 3px; min-width: 52px; text-align: center; }
.conditions p { color: var(--muted); font-size: 14px; }

.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--faint); font-size: 12px; font-weight: 600; }
tr.this { background: var(--accent-soft); }

.block { padding-top: 12px; border-top: 1px solid var(--line); }
.block .verdict { font-weight: 600; margin-bottom: 10px; }
.part { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; margin-top: 10px; }
.part ul { margin: 0; padding-left: 20px; }

.code { background: var(--editor); border: 1px solid var(--line); border-radius: 10px; padding: 12px 0; overflow-x: auto; }
.code table { font: 13px/1.6 var(--mono); }
.code td { border: 0; padding: 0 14px 0 0; white-space: pre; }
.code td.ln { width: 1%; padding: 0 14px; text-align: right; color: var(--faint); user-select: none; }

.foot { color: var(--faint); font-size: 12px; text-align: center; }

@media (max-width: 640px) {
  .report { padding: 32px 16px 56px; }
  .compare { grid-template-columns: 1fr; }
}
@media print {
  :root { color-scheme: light; --bg: #fff; --surface: #fff; --surface-2: #f6f8fa; --editor: #f6f8fa; --line: #d8dee4; --line-strong: #c4ccd5;
    --text: #1f2328; --muted: #57606a; --faint: #6e7781; --accent: #0969da; --green: #1a7f37; --amber: #9a6700; --red: #cf222e; }
  .report { padding: 0; }
  .card, .summary { break-inside: avoid; }
}
`;
