"use client";

/**
 * Main-line result (route === "person"): the person is the answer, so this
 * leads with the candidate cards and a sendable draft. Up to three candidates
 * are shown (top-ranked expanded). "この方に送る" confirms the hand-off — a
 * UI-only transition (the responder's accept/decline is 画面4 / #38); it never
 * dead-ends.
 */

import { CandidateCard } from "@/components/result/CandidateCard";
import { DraftEditor } from "@/components/result/DraftEditor";
import type { Recommendation } from "@/lib/api-types";
import { useState } from "react";

export interface PersonRouteViewProps {
  recommendations: Recommendation[];
  reason?: string;
  draft: string;
}

const MAX_CANDIDATES = 3;

export function PersonRouteView({ recommendations, reason, draft }: PersonRouteViewProps) {
  const candidates = recommendations.slice(0, MAX_CANDIDATES);
  const [selectedId, setSelectedId] = useState(candidates[0]?.person_id ?? "");
  const [sentTo, setSentTo] = useState<string | null>(null);

  const topCandidate = candidates[0];
  const selected = candidates.find((c) => c.person_id === selectedId) ?? topCandidate;
  // The draft is generated for the top candidate. Warn when a different
  // recipient is selected so the user edits it before sending (no misdirected
  // send). Per-recipient regeneration lands with the send wiring (#38).
  const draftMismatch = Boolean(selected) && selected?.person_id !== topCandidate?.person_id;

  function handleSend() {
    // Send to the currently selected candidate (not always the top one).
    setSentTo(selected?.name ?? "選択した担当者");
  }

  if (sentTo !== null) {
    return (
      <section className="mx-auto flex w-full max-w-2xl flex-col gap-md py-lg text-center">
        <h1 className="font-bold text-2xl text-primary">送信しました</h1>
        <p className="text-on-surface-variant">
          {sentTo}さんに依頼を送りました。返信があると通知でお知らせします。
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
              selected={candidate.person_id === selected?.person_id}
              onSelect={setSelectedId}
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

      {draftMismatch && topCandidate ? (
        <p className="rounded-lg border border-tertiary-container bg-surface-container-low p-sm text-on-surface-variant text-sm">
          下書きは最有力の{topCandidate.name}
          さん向けです。宛先を変える場合は本文を編集してください。
        </p>
      ) : null}

      {/*
       * Recipient changes remount this whole view (keyed by the top candidate in
       * ResultScreen), so DraftEditor is reset on a reroute without its own key.
       * A same-recipient late draft keeps the mount; edits are preserved by
       * DraftEditor's dirty guard.
       */}
      <DraftEditor initialDraft={draft} onSend={handleSend} />
    </section>
  );
}
