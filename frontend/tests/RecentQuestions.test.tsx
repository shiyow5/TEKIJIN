import { MOCK_RECENT_QUESTIONS, RecentQuestions } from "@/components/RecentQuestions";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("RecentQuestions", () => {
  it("renders the section heading", () => {
    render(<RecentQuestions />);
    expect(screen.getByRole("heading", { name: "最近あなたが解決した質問" })).toBeInTheDocument();
  });

  it("renders each mock question with its responder", () => {
    render(<RecentQuestions />);
    expect(screen.getByText("UTMの移行時の注意点")).toBeInTheDocument();
    expect(screen.getByText("高梨さん")).toBeInTheDocument();
    expect(screen.getByText("社内Wi-Fiの申請方法")).toBeInTheDocument();
    expect(screen.getByText("鈴木さん")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(MOCK_RECENT_QUESTIONS.length);
  });

  it("shows an empty state when there are no items", () => {
    render(<RecentQuestions items={[]} />);
    expect(screen.getByText("まだ解決済みの質問はありません。")).toBeInTheDocument();
  });
});
