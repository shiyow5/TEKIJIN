import { CandidateCard } from "@/components/result/CandidateCard";
import type { Recommendation } from "@/lib/api-types";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

function candidate(partial: Partial<Recommendation> = {}): Recommendation {
  return {
    person_id: "E007",
    name: "山本 修",
    dept: "情報システム部",
    score: 0.5,
    confidence: "中",
    reasons: [
      { type: "answers", detail: "類似の質問に過去2件回答" },
      { type: "proximity", detail: "全社から選定" },
      { type: "load", detail: "今週の対応件数: 少なめ" },
    ],
    ...partial,
  };
}

// Label + detail render as `<span><span>label</span>：detail</span>`; match on the
// outer span's full textContent so the split text nodes are read together.
const fullText = (text: string) => (_: string, el: Element | null) => el?.textContent === text;

describe("CandidateCard — comparison info on non-top cards (#204)", () => {
  it("shows distance and load VALUES even when the card is not expanded", () => {
    render(<CandidateCard candidate={candidate()} rank={2} expanded={false} selected={false} />);
    // 距離 and 現在の負荷 details are shown so 2nd/3rd can be compared to the top.
    expect(screen.getByText(fullText("距離の近さ：全社から選定"))).toBeInTheDocument();
    expect(screen.getByText(fullText("現在の負荷：今週の対応件数: 少なめ"))).toBeInTheDocument();
  });

  it("still keeps non-comparison reason details compact when not expanded", () => {
    render(<CandidateCard candidate={candidate()} rank={2} expanded={false} selected={false} />);
    // The past-answer reason shows only its label (outer span text == the label,
    // i.e. no ：detail was appended), not the verbatim detail.
    expect(screen.getAllByText(fullText("過去回答")).length).toBeGreaterThan(0);
    expect(screen.queryByText(/類似の質問に過去2件回答/)).not.toBeInTheDocument();
  });

  it("shows every reason's detail on the expanded top card", () => {
    render(<CandidateCard candidate={candidate()} rank={1} expanded={true} selected={true} />);
    expect(screen.getByText(fullText("過去回答：類似の質問に過去2件回答"))).toBeInTheDocument();
    expect(screen.getByText(fullText("距離の近さ：全社から選定"))).toBeInTheDocument();
  });
});
