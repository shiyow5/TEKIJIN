"use client";

/**
 * 直接相談のふりかえり画面 (#247).
 *
 * Loads `GET /consult-retrospective/{session_id}` so the write-up can be
 * attributed to the right question and the right responder, then renders
 * {@link RetrospectiveForm}.
 *
 * That endpoint — rather than `GET /handoff` — is what makes the screen reachable
 * at all: the hand-off view is the PENDING view and 404s as soon as the responder
 * records an outcome, which is the moment the face-to-face consultation becomes
 * possible in the first place.
 *
 * Deliberately refuses rather than rendering a form that cannot produce a valid
 * record: a chat hand-off (the transcript already exists — a hearsay summary on
 * top of it would be a second, weaker copy), a hand-off nobody has accepted yet
 * (nothing was consulted), and one that has already been written up.
 */

import { useEffect, useState } from "react";

import { PageBackLink } from "@/components/PageBackLink";
import { RetrospectiveForm } from "@/components/RetrospectiveForm";
import { getRetrospectiveContext } from "@/lib/api-client";
import type { ConsultRetrospectiveContext } from "@/lib/api-types";

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
  const [context, setContext] = useState<ConsultRetrospectiveContext | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getRetrospectiveContext(sessionId)
      .then((data) => {
        if (!cancelled) {
          setContext(data);
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
      ) : context === null ? (
        <p className="text-on-surface-variant">読み込み中...</p>
      ) : context.consult_method !== "direct" ? (
        <Notice>
          この依頼はチャットで相談しているため、ふりかえりの入力は不要です。
          チャットのやり取りが残っているので、そちらがそのままナレッジになります。
        </Notice>
      ) : context.responder === null ? (
        <Notice>
          この依頼はまだ受諾されていないため、ふりかえりを残せません。
          相談が済んでから、もう一度開いてください。
        </Notice>
      ) : context.already_recorded ? (
        <Notice>この相談のふりかえりは、すでに記録されています。</Notice>
      ) : (
        <>
          <p className="whitespace-pre-wrap text-on-surface-variant">{context.question}</p>
          <RetrospectiveForm
            questionId={context.question_id}
            responderId={context.responder.person_id}
            responderName={context.responder.name}
          />
        </>
      )}
    </section>
  );
}
