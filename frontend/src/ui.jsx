import { useEffect } from "react";

export function ToastHost({ toasts }) {
  return (
    <div id="toasts">
      {toasts.map((t) => (
        <div className="toast" key={t.id}>
          {t.k ? <span className="k">{t.k}</span> : null}
          {t.msg}
        </div>
      ))}
    </div>
  );
}

export function Modal({ title, wide, children, foot, onClose }) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay on" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={"modal" + (wide ? " wide" : "")}>
        <div className="modal-h">
          <span className="t">{title}</span>
          <button className="x" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>
        <div className="modal-b">{children}</div>
        <div className="modal-f">{foot}</div>
      </div>
    </div>
  );
}

export function Field({ label, req, hint, children }) {
  return (
    <div className="field">
      <label>
        {label}
        {req ? <span className="req">*</span> : null}
      </label>
      {children}
      {hint ? <div className="hint">{hint}</div> : null}
    </div>
  );
}
