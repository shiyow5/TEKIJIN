"use client";

/**
 * Main-line result (route === "person"): the person is the answer, so this
 * leads with the candidate cards and a sendable draft. Up to three candidates
 * are shown; the asker may pick any of them as the recipient, not just the top
 * pick (#200) — picking one calls `POST /handoff/select`, which reorders the
 * durable hand-off target and regenerates the draft for that person. Only the
 * top card is `expanded`; the comparison signals 距離/現在の負荷 are surfaced on
 * every card by `CandidateCard` itself (#204).
 * "この内容で依頼する" then persists the (possibly further-edited) draft text
 * to the pending hand-off (POST /handoff/draft), so the responder reads the
 * edited text — it is a real send, not a UI-only transition (#174).
 */

import { CandidateCard } from "@/components/result/CandidateCard";
import { DraftEditor } from "@/components/result/DraftEditor";
import { selectHandoffCandidate, updateHandoffDraft } from "@/lib/api-client";
import type { Recommendation } from "@/lib/api-types";
import { fitPercents } from "@/lib/fit";
import { useEffect, useRef, useState } from "react";

export interface PersonRouteViewProps {
  recommendations: Recommendation[];
  reason?: string;
  draft: string;
  /** Session id for the confirm POST; null when rendered outside a session. */
  sessionId: string | null;
}

const MAX_CANDIDATES = 3;
const SEND_ERROR = "送信に失敗しました。時間をおいて、もう一度お試しください。";
const SELECT_ERROR = "候補の切り替えに失敗しました。時間をおいて、もう一度お試しください。";

export function PersonRouteView({
  recommendations,
  reason,
  draft,
  sessionId,
}: PersonRouteViewProps) {
  const candidates = recommendations.slice(0, MAX_CANDIDATES);
  // Absolute fit % from each candidate's score, decoupled from the 高/中/低
  // confidence label (#240): a strong #1 on a never-asked topic (label 低) still
  // reads high, and the label rides along as a separate evidence badge.
  const fitValues = fitPercents(candidates);
  const [selectedPersonId, setSelectedPersonId] = useState(candidates[0]?.person_id ?? null);
  const [localDraft, setLocalDraft] = useState(draft);
  const [selecting, setSelecting] = useState(false);
  const [sending, setSending] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A later-arriving `draft` SSE event (recommend can land before draft) updates
  // the baseline shown in the editor; a reselect's response (see handleSelect)
  // overrides this locally without waiting on a new SSE event.
  useEffect(() => {
    setLocalDraft(draft);
  }, [draft]);

  // A decline→reroute remounts this view (keyed by the top candidate in
  // ResultScreen) while a confirm POST may still be in flight. Drop the post-await
  // state updates if that happened, matching the project's async-guard convention.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  async function handleSelect(personId: string) {
    if (selecting || personId === selectedPersonId || !sessionId) return;
    setSelecting(true);
    setError(null);
    try {
      const result = await selectHandoffCandidate({ session_id: sessionId, person_id: personId });
      if (mounted.current) {
        setSelectedPersonId(personId);
        setLocalDraft(result.draft);
      }
    } catch {
      // 404 = the hand-off was already answered/closed; 409 = a clarification is
      // owed; 422 = the candidate is no longer among the shown recommendations.
      if (mounted.current) setError(SELECT_ERROR);
    } finally {
      if (mounted.current) setSelecting(false);
    }
  }

  async function handleSend(text: string) {
    if (!sessionId) {
      setError(SEND_ERROR);
      return;
    }
    setSending(true);
    setError(null);
    try {
      await updateHandoffDraft({ session_id: sessionId, draft: text });
      const selected = candidates.find((c) => c.person_id === selectedPersonId);
      if (mounted.current) setSentTo(selected?.name ?? candidates[0]?.name ?? "ご担当者");
    } catch {
      // 404 = the hand-off was already answered/closed; 409 = a clarification is
      // owed; either way the send can't land, so surface a retryable error.
      if (mounted.current) setError(SEND_ERROR);
    } finally {
      if (mounted.current) setSending(false);
    }
  }

  if (sentTo !== null) {
    return (
      <section className="mx-auto flex w-full max-w-2xl flex-col gap-md py-lg text-center">
        <h1 className="font-bold text-2xl text-primary">依頼を送りました</h1>
        <p className="text-on-surface-variant">
          {sentTo}さんに、この内容でお繋ぎしました。返信があるとお知らせします。
        </p>
        <div className="flex justify-center">
          <a
            href="/questions"
            className="min-h-[48px] rounded-full bg-primary px-lg py-sm font-bold text-on-primary shadow-md transition-colors hover:bg-primary-container"
          >
            新しい質問をする
          </a>
        </div>
      </section>
    );
  }

  return (
    <section className="mx-auto flex w-full max-w-5xl flex-col gap-md py-lg">
      <header className="flex flex-col gap-xs">
        <h1 className="font-bold text-2xl text-on-surface">この質問は、人に聞くのが確実です</h1>
        <p className="text-on-surface-variant">
          {reason || "直近で同様の案件を担当した方の知見が役立ちそうです。"}
        </p>
      </header>

      {candidates.length > 0 ? (
        <div className="grid grid-cols-1 gap-md md:grid-cols-3">
          {candidates.map((candidate, index) => (
            <CandidateCard
              key={candidate.person_id}
              candidate={candidate}
              rank={index + 1}
              expanded={index === 0}
              selected={candidate.person_id === selectedPersonId}
              onSelect={sessionId ? handleSelect : undefined}
              fitPercent={fitValues[index]}
            />
          ))}
        </div>
      ) : (
        // Graceful fallback: a reconnect at the send interrupt can replay the
        // draft without the candidates. Keep the draft sendable rather than
        // dead-ending.
        <p className="rounded-lg border border-outline-variant bg-surface-container-low p-md text-on-surface-variant text-sm">
          宛先候補を再取得しています。この下書きはそのまま送れます。
        </p>
      )}

      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-error-container bg-error-container p-sm text-on-error-container text-sm"
        >
          {error}
        </p>
      ) : null}

      <DraftEditor
        key={selectedPersonId}
        initialDraft={localDraft}
        disabled={sending || selecting}
        onSend={handleSend}
      />
    </section>
  );
}
