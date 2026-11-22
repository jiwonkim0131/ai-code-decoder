// 코드 입력창의 IDE식 편집 규칙(Python 기준). 화면에서만 쓰고, 분석은 서버가 원문 그대로 받는다.

export const INDENT = "    ";
const BLOCK_END = /^(return|pass|raise|break|continue)\b/;

/** 붙여넣은 코드를 정리한다: 줄바꿈·탭·특수 공백 통일, 줄 끝 공백 제거, 공통 들여쓰기 제거,
 *  앞뒤 빈 줄 제거, 연속 빈 줄은 2줄까지. 함수 안 일부만 복사해도 문법 오류가 나지 않게 한다. */
export function formatPasted(text: string): string {
  let lines = text
    .replace(/\r\n?/g, "\n")
    .replace(/[   ]/g, " ")
    .split("\n")
    .map((line) => line.replace(/^[ \t]+/, (lead) => lead.replace(/\t/g, INDENT)).replace(/\s+$/, ""));

  while (lines.length > 1 && lines[0] === "") lines.shift();
  while (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();

  const indents = lines.filter((l) => l).map((l) => l.length - l.trimStart().length);
  const common = indents.length ? Math.min(...indents) : 0;
  if (common > 0) lines = lines.map((l) => l.slice(common));

  const out: string[] = [];
  let blanks = 0;
  for (const line of lines) {
    blanks = line ? 0 : blanks + 1;
    if (blanks <= 2) out.push(line);
  }
  return out.join("\n");
}

/** 붙여넣는 자리의 들여쓰기에 맞춰 둘째 줄부터 들여쓴다(첫 줄은 커서 위치에 그대로 붙는다). */
export function reindent(text: string, base: string): string {
  if (!base) return text;
  return text
    .split("\n")
    .map((line, i) => (i === 0 || !line ? line : base + line))
    .join("\n");
}

export function leadingSpaces(line: string): string {
  return line.match(/^ */)![0];
}

/** Enter 뒤 새 줄의 들여쓰기: 이전 줄을 따르되 ':'로 끝나면 한 단계 더, return 등 뒤에는 한 단계 덜. */
export function nextIndent(lineBeforeCursor: string): string {
  const indent = leadingSpaces(lineBeforeCursor);
  const body = lineBeforeCursor.replace(/#.*$/, "").trimEnd();
  if (body.endsWith(":")) return indent + INDENT;
  if (BLOCK_END.test(body.trimStart())) return indent.slice(INDENT.length);
  return indent;
}
