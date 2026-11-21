import { useId, useState, type ReactNode } from "react";
import type { MarkdownSkill } from "./chat";
import { Icon } from "./Icon";

type Entry = { group?: NonNullable<MarkdownSkill["group"]>; items: MarkdownSkill[] };

export function skillEntries(skills: MarkdownSkill[]): Entry[] {
  const groups = new Map<string, Entry>();
  const entries: Entry[] = [];
  for (const skill of skills) {
    const group = skill.group;
    if (!group) {
      entries.push({ items: [skill] });
      continue;
    }
    let entry = groups.get(group.key);
    if (!entry) {
      entry = { group, items: [] };
      groups.set(group.key, entry);
      entries.push(entry);
    }
    entry.items.push(skill);
  }
  return entries;
}

function GroupRow({ entry, visible, selected, searching, toggle, render }: {
  entry: Entry;
  visible: MarkdownSkill[];
  selected: string[];
  searching: boolean;
  toggle?: (ids: string[]) => void;
  render: (skill: MarkdownSkill) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const name = entry.group!.name;
  const ids = entry.items.map(s => s.id);
  const count = ids.filter(id => selected.includes(id)).length;
  const open = expanded || searching;
  return <li className="skill-bundle">
    <div className="skill-bundle-heading">
      {toggle && <input type="checkbox" aria-label={"Весь комплект «" + name + "»"}
        checked={count === ids.length}
        ref={el => { if (el) el.indeterminate = count > 0 && count < ids.length; }}
        onChange={() => toggle(ids)} />}
      <button type="button" aria-label={"Операции «" + name + "»"} aria-expanded={open} aria-controls={listId}
        onClick={() => setExpanded(!expanded)}>
        <Icon name="skills" size={18} />
        <strong>{name}</strong>
        <small>{toggle ? count + " / " + ids.length : ids.length}</small>
        <span className="group-chevron"><Icon name="chevron" size={16} /></span>
      </button>
    </div>
    <ul id={listId} hidden={!open} className="skill-bundle-children">
      {visible.map(s => <li key={s.id}>{render(s)}</li>)}
    </ul>
  </li>;
}

export function SkillGroups({ entries, selected, toggle, query = "", render, className }: {
  entries: Entry[];
  selected: string[];
  toggle?: (ids: string[]) => void;
  query?: string;
  render: (skill: MarkdownSkill) => ReactNode;
  className: string;
}) {
  const term = query.trim().toLocaleLowerCase();
  const rows = entries.flatMap(entry => {
    const groupMatch = entry.group?.name.toLocaleLowerCase().includes(term);
    const visible = entry.items.filter(s => groupMatch ||
      (s.name + " " + s.description).toLocaleLowerCase().includes(term));
    if (!visible.length) return [];
    if (!entry.group) return [<li key={visible[0].id}>{render(visible[0])}</li>];
    return [<GroupRow key={entry.group.key} entry={entry} visible={visible} selected={selected}
      searching={!!term} toggle={toggle} render={render} />];
  });
  return rows.length ? <ul className={className}>{rows}</ul> :
    <p>{term ? "Совпадений нет." : "Добавьте первый навык в библиотеке."}</p>;
}
