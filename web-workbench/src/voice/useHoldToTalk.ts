import { useCallback, useEffect, useRef, type RefObject } from "react";

type HoldSource = "keyboard" | "pointer";

type InputModality = "keyboard" | "pointer";

/** Space (legacy "Spacebar" for old WebKits) and Enter both drive push-to-talk. */
const TALK_KEYS = new Set([" ", "Enter", "Spacebar"]);

const TEXT_ENTRY_SELECTOR = [
  "input",
  "textarea",
  "select",
  '[contenteditable="true"]',
  '[contenteditable=""]',
  '[role="textbox"]',
  '[role="combobox"]',
].join(", ");

/** While one of these is open the shortcut must not hijack the keyboard. */
const MODAL_SELECTOR = [
  '[role="dialog"][aria-modal="true"]',
  '[role="menu"]',
  '[role="listbox"]',
].join(", ");

const NATIVE_CONTROL_SELECTOR = [
  "button",
  "a[href]",
  "summary",
  '[role="button"]',
  '[role="link"]',
  '[role="menuitem"]',
  '[role="option"]',
  '[role="tab"]',
  '[role="switch"]',
  '[role="checkbox"]',
  '[role="radio"]',
].join(", ");

function isTalkKey(event: KeyboardEvent): boolean {
  return TALK_KEYS.has(event.key);
}

function isTextEntry(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return target.closest(TEXT_ENTRY_SELECTOR) !== null;
}

/**
 * A focused control keeps its native Space/Enter activation when the user got
 * there with the keyboard (Tab). Focus left behind by a mouse click keeps the
 * page-wide shortcut, which is what the "按住空格/回车说话" hint promises.
 *
 * Note: :focus-visible is not usable here — Chromium re-evaluates it on the
 * keydown itself, so a mouse-focused button starts matching right when Space
 * is pressed. Track the last input modality instead.
 */
function keepsNativeActivation(
  target: EventTarget | null,
  talkButton: HTMLElement | null,
  modality: InputModality,
): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const control = target.closest(NATIVE_CONTROL_SELECTOR);
  if (!control || control === talkButton) return false;
  return modality === "keyboard";
}

function isModalOpen(): boolean {
  return document.querySelector(MODAL_SELECTOR) !== null;
}

/**
 * Hold-to-talk press state shared by pointer and keyboard input.
 *
 * Keyboard handling is page-wide on purpose: the composer button is rarely
 * focused, so binding Space/Enter to the button alone made the documented
 * shortcut do nothing (it just scrolled the page).
 */
export function useHoldToTalk({
  enabled,
  onStart,
  onStop,
  buttonRef,
}: {
  /** False while the voice socket is unavailable or already recording. */
  enabled: boolean;
  onStart: () => void;
  onStop: () => void;
  buttonRef: RefObject<HTMLElement | null>;
}) {
  const holdingRef = useRef(false);
  const sourceRef = useRef<HoldSource | null>(null);
  const modalityRef = useRef<InputModality>("keyboard");
  const latestRef = useRef({ enabled, onStart, onStop });
  latestRef.current = { enabled, onStart, onStop };

  const release = useCallback((source?: HoldSource) => {
    if (!holdingRef.current) return;
    if (source && sourceRef.current !== source) return;
    holdingRef.current = false;
    sourceRef.current = null;
    latestRef.current.onStop();
  }, []);

  const hold = useCallback((source: HoldSource) => {
    if (holdingRef.current || !latestRef.current.enabled) return;
    holdingRef.current = true;
    sourceRef.current = source;
    latestRef.current.onStart();
  }, []);

  useEffect(() => {
    // A click leaves focus on the clicked control, but it is still a pointer
    // session: the Space/Enter shortcut must stay available afterwards.
    function onPointerDown() {
      modalityRef.current = "pointer";
    }

    function trackModality(event: KeyboardEvent) {
      if (event.key === "Tab") modalityRef.current = "keyboard";
    }

    function onKeyDown(event: KeyboardEvent) {
      if (!isTalkKey(event)) return;
      if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (!latestRef.current.enabled) return;
      if (isTextEntry(event.target) || isModalOpen()) return;
      if (keepsNativeActivation(event.target, buttonRef.current, modalityRef.current)) {
        return;
      }
      // Space would otherwise scroll the transcript.
      event.preventDefault();
      if (event.repeat) return;
      hold("keyboard");
    }

    function onKeyUp(event: KeyboardEvent) {
      if (!isTalkKey(event)) return;
      if (!holdingRef.current || sourceRef.current !== "keyboard") return;
      event.preventDefault();
      release("keyboard");
    }

    function onPointerEnd() {
      release("pointer");
    }

    function onBlur() {
      release();
    }

    function onVisibilityChange() {
      if (document.visibilityState === "hidden") release();
    }

    // Capture phase so the modality is known before any bubble handler runs.
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("keydown", trackModality, true);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    // Releases that never reach the button: pointer abandoned outside the
    // window, tab switched, or the whole window losing focus mid-hold.
    window.addEventListener("pointerup", onPointerEnd);
    window.addEventListener("pointercancel", onPointerEnd);
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("keydown", trackModality, true);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("pointerup", onPointerEnd);
      window.removeEventListener("pointercancel", onPointerEnd);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [buttonRef, hold, release]);

  return { hold, release };
}
