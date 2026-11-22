// 공통 드롭다운. 네이티브 <select>는 펼친 목록을 OS가 그려 다크 톤과 맞지 않아 직접 그린다.
// 키보드·스크린리더 동작은 ARIA listbox 패턴(버튼 + 목록, aria-activedescendant)을 따른다.
import { useEffect, useId, useRef, useState } from "react";

export type SelectOption<T extends string> = {
  value: T;
  label: string;
  hint?: string; // 오른쪽에 흐리게 붙는 보조 정보(버전 등)
  disabled?: boolean;
};

export function Select<T extends string>(props: {
  value: T;
  options: SelectOption<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
  id?: string;
}) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);

  const selected = props.options.find((o) => o.value === props.value) ?? props.options[0];
  const enabled = props.options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0);

  useEffect(() => {
    if (!open) return;
    list.current?.focus();
    const outside = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);

  const show = () => {
    setActive(Math.max(0, props.options.indexOf(selected)));
    setOpen(true);
  };

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };

  const choose = (index: number) => {
    const option = props.options[index];
    if (!option || option.disabled) return;
    if (option.value !== props.value) props.onChange(option.value);
    close();
  };

  const move = (step: number) => {
    const at = enabled.indexOf(active);
    const next = enabled[Math.min(enabled.length - 1, Math.max(0, (at === -1 ? 0 : at) + step))];
    if (next !== undefined) setActive(next);
  };

  const onListKey = (e: React.KeyboardEvent) => {
    const keys: Record<string, () => void> = {
      ArrowDown: () => move(1),
      ArrowUp: () => move(-1),
      Home: () => setActive(enabled[0] ?? 0),
      End: () => setActive(enabled[enabled.length - 1] ?? 0),
      Enter: () => choose(active),
      " ": () => choose(active),
      Escape: () => close(),
    };
    if (e.key === "Tab") return close(false);
    const action = keys[e.key];
    if (!action) return;
    e.preventDefault();
    action();
  };

  return (
    <div className={`select ${open ? "open" : ""}`} ref={root}>
      <button
        ref={trigger}
        id={props.id}
        type="button"
        className="select-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={`${props.ariaLabel}: ${selected.label}`}
        onClick={() => (open ? close() : show())}
        onKeyDown={(e) => {
          if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
            e.preventDefault();
            show();
          }
        }}
      >
        <span className="select-value">{selected.label}</span>
        <svg className="select-chevron" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <ul
          ref={list}
          id={listId}
          className="select-menu"
          role="listbox"
          tabIndex={-1}
          aria-label={props.ariaLabel}
          aria-activedescendant={`${listId}-${active}`}
          onKeyDown={onListKey}
        >
          {props.options.map((o, i) => (
            <li
              key={o.value}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={o.value === props.value}
              aria-disabled={o.disabled || undefined}
              className={`select-option ${i === active ? "active" : ""}`}
              onMouseEnter={() => !o.disabled && setActive(i)}
              onClick={() => choose(i)}
            >
              <span>{o.label}</span>
              {o.hint && <span className="select-hint">{o.hint}</span>}
              <svg className="select-check" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
                <path
                  d="M3 7.2 5.8 10 11 4"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
