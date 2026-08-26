"use client";

/**
 * 直接相談のふりかえり画面 (#247).
 *
 * Loads the session's hand-off so the write-up can be attributed to the right
 * question and the right responder, then renders {@link RetrospectiveForm}.
 *
 * Deliberately refuses in three cases rather than rendering a form that cannot
 * produce a valid record: a chat hand-off (the transcript already exists — a
 * hearsay summary on top of it would be a second, weaker copy), a hand-off with
 * no responder, and one with no question id.
 */

import { useEffect, useState } from "react";

import { PageBackLink } from "@/components/PageBackLink";
import { RetrospectiveForm } from "@/components/RetrospectiveForm";
import { getHandoff } from "@/lib/api-client";
import type { HandoffResponse } from "@/lib/api-types";

export interface RetrospectiveScreenProps {
  sessionId: string;
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <p role="alert" className="text-on-surface-variant">
      {children}
    </p>
  );
}

export function RetrospectiveScreen({ sessionId }: RetrospectiveScreenProps) {
  const [handoff, setHandoff] = useState<HandoffResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getHandoff(sessionId)
      .then((data) => {
        if (!cancelled) {
          setHandoff(data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  return (
    <section className="mx-auto flex w-full max-w-2xl flex-col gap-md py-lg">
      <PageBackLink href="/history" label="質問履歴へ戻る" />
      <h1 className="font-bold text-2xl text-on-surface">相談のふりかえり</h1>
      {failed ? (
        <Notice>この依頼を読み込めませんでした。時間をおいて開き直してください。</Notice>
      ) : handoff === null ? (
        <p className="text-on-surface-variant">読み込み中...</p>
      ) : handoff.consult_method !== "direct" ? (
        <Notice>
          この依頼はチャットで相談しているため、ふりかえりの入力は不要です。
          チャットのやり取りが残っているので、そちらがそのままナレッジになります。
        </Notice>
      ) : handoff.responder == null ? (
        <Notice>この依頼には対応者が記録されていないため、ふりかえりを残せません。</Notice>
      ) : handoff.question_id == null ? (
        <Notice>この依頼には質問が紐づいていないため、ふりかえりを残せません。</Notice>
      ) : (
        <>
          <p className="whitespace-pre-wrap text-on-surface-variant">{handoff.question}</p>
          <RetrospectiveForm
            questionId={handoff.question_id}
            responderId={handoff.responder.person_id}
            responderName={handoff.responder.name}
          />
        </>
      )}
    </section>
  );
}
