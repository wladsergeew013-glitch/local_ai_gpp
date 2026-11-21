import { memo } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const plugins = [remarkGfm];
const components: Components = {
  table: ({ children }) => (
    <div
      className="answer-table"
      tabIndex={0}
      role="region"
      aria-label="Таблица ответа"
    >
      <table>{children}</table>
    </div>
  ),
  img: ({ alt }) => <span>{alt}</span>,
  a: ({ children, href }) =>
    href && /^https?:\/\//i.test(href) ? (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    ) : (
      <>{children}</>
    ),
};

export const MarkdownAnswer = memo(function MarkdownAnswer({
  text,
}: {
  text: string;
}) {
  return (
    <div className="markdown-answer">
      <Markdown skipHtml remarkPlugins={plugins} components={components}>
        {text}
      </Markdown>
    </div>
  );
});
