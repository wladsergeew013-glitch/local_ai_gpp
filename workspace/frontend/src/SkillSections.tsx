import type { MarkdownSkill } from "./chat";

export function SkillSections({
  skill,
  disabled,
  toggle,
}: {
  skill: MarkdownSkill;
  disabled: string[];
  toggle: (section: string) => void;
}) {
  if (!skill.sections?.length) return null;
  return (
    <details className="skill-sections">
      <summary>
        Пункты навыка{" "}
        <span>
          {
            skill.sections.filter((section) => !disabled.includes(section.id))
              .length
          }
          /{skill.sections.length}
        </span>
      </summary>
      <div>
        {skill.sections.map((section) => (
          <label key={section.id}>
            <input
              type="checkbox"
              checked={!disabled.includes(section.id)}
              onChange={() => toggle(section.id)}
            />
            <span>{section.title}</span>
          </label>
        ))}
      </div>
    </details>
  );
}
