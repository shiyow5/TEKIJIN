"use client";

/**
 * "直接相談" / "チャットで相談" badge (#245).
 *
 * The responder has to see this BEFORE deciding to accept: with "直接相談" no
 * chat thread is ever created (`data/messages.py` gates on it), so accepting
 * means being approached some other way — a different commitment than a chat.
 * Rendered in the inbox list and on the answer screen's pre-accept view.
 */

import type { ConsultMethod } from "@/lib/api-types";

const LABEL: Record<ConsultMethod, string> = {
  direct: "直接相談",
  chat: "チャットで相談",
};

const STYLE: Record<ConsultMethod, string> = {
  direct: "bg-tertiary-container text-on-tertiary-container",
  chat: "bg-secondary-container text-on-secondary-container",
};

export function ConsultMethodBadge({ method }: { method: ConsultMethod }) {
  return (
    <span className={`shrink-0 rounded-full px-sm py-[2px] font-medium text-xs ${STYLE[method]}`}>
      {LABEL[method]}
    </span>
  );
}
