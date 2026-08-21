/**
 * Voice-input (🎤) button.
 *
 * Speech recognition is not wired in #35 — this renders the affordance in its
 * place in the input row so the layout matches the design (template `mic`
 * button). The button is intentionally inert (marked "準備中") until voice
 * capture lands in a later task.
 */

export interface VoiceInputButtonProps {
  /** Optional handler; when omitted the button is a no-op placeholder. */
  onActivate?: () => void;
  disabled?: boolean;
}

export function VoiceInputButton({ onActivate, disabled = false }: VoiceInputButtonProps) {
  return (
    <button
      type="button"
      aria-label="音声入力（準備中）"
      title="音声入力（準備中）"
      onClick={onActivate}
      disabled={disabled}
      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-on-surface-variant transition-colors hover:bg-surface-container-low hover:text-primary disabled:cursor-not-allowed"
    >
      <span aria-hidden="true" className="text-lg">
        🎤
      </span>
    </button>
  );
}
