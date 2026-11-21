import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import "./skill-files.css";
import { createPortal } from "react-dom";
import { SkillGroups, skillEntries } from "./SkillGroups";
import { SkillSections } from "./SkillSections";
import { Icon } from "./Icon";
import type { MarkdownSkill } from "./chat";

type Props = {
  skills: MarkdownSkill[];
  selectedIds: string[];
  disabled: boolean;
  toggle: (ids: string[]) => void;
  sections: Record<string, string[]>;
  toggleSection: (id: string, key: string) => void;
  library: () => void;
};

export function SkillControls({
  skills,
  selectedIds,
  disabled,
  toggle,
  library,
  sections,
  toggleSection,
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [position, setPosition] = useState<CSSProperties>({});
  const trigger = useRef<HTMLButtonElement>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const selected = selectedIds.flatMap(
    (id) => skills.find((s) => s.id === id) || [],
  );
  const entries = skillEntries(skills);
  const picked = entries.filter(entry => entry.items.some(item => selectedIds.includes(item.id)));
  const single = picked.length === 1 ? picked[0] : undefined;
  let summary = "Навыки";
  if (single?.group) {
    summary = single.group.name;
    if (selected.length < single.items.length) summary += " · " + selected.length + "/" + single.items.length;
  } else if (single) {
    summary = single.items[0].name;
  } else if (picked.length) {
    summary += " · " + picked.length;
  }

  function close() {
    setOpen(false);
    trigger.current?.focus();
  }
  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      const anchor = trigger.current?.getBoundingClientRect();
      if (!anchor) return;
      const width = Math.min(376, window.innerWidth - 20);
      const above = anchor.top - 12;
      const below = window.innerHeight - anchor.bottom - 12;
      const up = above >= 360 || above >= below;
      setPosition({
        width,
        left: Math.max(
          10,
          Math.min(anchor.left, window.innerWidth - width - 10),
        ),
        maxHeight: Math.max(80, up ? above : below),
        ...(up
          ? { bottom: window.innerHeight - anchor.top + 6 }
          : { top: anchor.bottom + 6 }),
      });
    }
    place();
    menu.current?.focus({ preventScroll: true });
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (e: Event) => {
      const el = e.target as Node;
      if (!menu.current?.contains(el) && !wrapper.current?.contains(el))
        setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
      }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", key);
    };
  }, [open]);
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);
  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  return (
    <div className="skill-controls" ref={wrapper}>
      <button
        ref={trigger}
        type="button"
        disabled={disabled}
        className={
          "tool-button skill-trigger" + (selected.length ? " enabled" : "")
        }
        aria-label="Навыки диалога"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title={
          selected.length
            ? selected.map((s) => s.name).join(", ")
            : "Доступные модели навыки"
        }
        onClick={() => setOpen(!open)}
      >
        <Icon name="skills" size={16} />
        <span>{summary}</span>
        <Icon name="chevron" size={14} />
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            id={menuId}
            role="dialog"
            aria-label="Выбор навыков"
            tabIndex={-1}
            className="skill-menu"
            style={position}
          >
            <header>
              <strong>Навыки диалога</strong>
              <span>Доступно модели · {selected.length}</span>
            </header>
            {skills.length > 5 && (
              <input
                className="skill-search"
                aria-label="Поиск навыков"
                placeholder="Найти навык…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            )}
            <div className="skill-menu-list">
              <SkillGroups className="skill-pick-list" entries={entries} selected={selectedIds} toggle={toggle} query={query} render={s => {
                const checked = selectedIds.includes(s.id);
                return (
                  <div
                    key={s.id}
                    className={"skill-menu-item" + (checked ? " checked" : "")}
                  >
                    <label>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle([s.id])}
                        aria-label={s.name}
                      />
                      <span>
                        <strong>{s.name}</strong>
                        {s.description && <small>{s.description}</small>}
                        {!!s.formats?.length && <small>{s.formats.join(" · ")}</small>}
                      </span>
                    </label>
                    {checked && (
                      <SkillSections
                        skill={s}
                        disabled={sections[s.id] || []}
                        toggle={(key) => toggleSection(s.id, key)}
                      />
                    )}
                  </div>
                );
              }} />
            </div>
            <footer>
              <button
                type="button"
                className="tool-button"
                onClick={() => {
                  close();
                  library();
                }}
              >
                <Icon name="skills" size={15} />
                Библиотека
              </button>
              <button type="button" className="primary" onClick={close}>
                Готово
              </button>
            </footer>
          </div>,
          document.body,
        )}
    </div>
  );
}
