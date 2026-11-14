import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function SettingsDialog({
  children,
  onClose,
  error,
}: {
  children: ReactNode;
  onClose: () => void;
  error?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = ref.current;
    dialog?.showModal();
    return () => {
      dialog?.close();
      previous?.focus();
    };
  }, []);
  return createPortal(
    <dialog
      ref={ref}
      className="settings-dialog"
      aria-label="Настройки Workspace"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const box = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < box.left ||
          event.clientX > box.right ||
          event.clientY < box.top ||
          event.clientY > box.bottom
        )
          onClose();
      }}
    >
      {error && (
        <div className="dialog-error" role="alert">
          {error}
        </div>
      )}
      {children}
    </dialog>,
    document.body,
  );
}
