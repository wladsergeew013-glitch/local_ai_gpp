const paths = {
  settings: "M4 7h8m4 0h4M4 17h3m4 0h9M12 4v6M8 14v6",
  assistant:
    "M6 5h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-7l-5 3v-3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Zm3 6h.01M15 11h.01",
  runtime:
    "M5 4h14a1 1 0 0 1 1 1v5H4V5a1 1 0 0 1 1-1ZM4 14h16v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1Zm3-7h.01M7 17h.01",
  skills: "m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
  data: "M4 5h16v4H4zM6 9v10h12V9m-8 4h4",
  file: "M6 3h8l4 4v14H6zM14 3v5h4M9 13h6m-6 4h4",
  clip: "m8.5 13.5 6.7-6.7a2.1 2.1 0 0 1 3 3l-8.3 8.3a4 4 0 1 1-5.7-5.7l8.5-8.5a5.5 5.5 0 0 1 7.8 7.8L12 20.2",
  close: "m6 6 12 12M18 6 6 18",
  plus: "M12 5v14M5 12h14",
  chevron: "m8 10 4 4 4-4",
  send: "M12 19V5m-6 6 6-6 6 6",
  external:
    "M13 4h7v7m0-7L10 14m-1-9H5a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1v-4",
  plug: "m11 5 3 3m1-7 3 3M8 7l9 9m-7-7-4 4a4 4 0 0 0 5 5l4-4m-8 3-4 4",
  check: "m5 12 4 4L19 6",
  memory:
    "M7 4h11a2 2 0 0 1 2 2v13H8a3 3 0 0 1-3-3V6a2 2 0 0 1 2-2ZM5 16a2 2 0 0 1 2-2h13M9 8h6",
} as const;

export type IconName = keyof typeof paths;
export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={paths[name]} />
    </svg>
  );
}
