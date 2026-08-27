import {
  QuestionStructurePanel,
  composeStructuredDraft,
} from "@/components/result/QuestionStructurePanel";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const structureQuestionMock = vi.fn();
vi.mock("@/lib/api-client", () => ({
  structureQuestion: (...args: unknown[]) => structureQuestionMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

beforeEach(() => {
  structureQuestionMock.mockReset();
});

describe("composeStructuredDraft", () => {
  it("labels each non-empty field and drops the blanks", () => {
    const out = composeStructuredDraft({
      summary: "docker が失敗する",
      environment: "",
      tried: "  ",
      blocker: "原因不明",
    });
    expect(out).toBe("【起きていること】\ndocker が失敗する\n\n【詰まっている点】\n原因不明");
  });

  it("returns an empty string when every field is blank", () => {
    expect(composeStructuredDraft({ summary: "", environment: "", tried: "", blocker: "" })).toBe(
      "",
    );
  });
});

describe("QuestionStructurePanel", () => {
  it("fetches on demand and reveals the four editable fields", async () => {
    structureQuestionMock.mockResolvedValue({
      session_id: "s1",
      summary: "docker が失敗する",
      environment: "M2 Mac",
      tried: "",
      blocker: "原因不明",
    });
    render(<QuestionStructurePanel sessionId="s1" onApply={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "AIに質問を整理してもらう" }));

    // The four fields appear, seeded from the response; empty ones stay editable.
    expect(await screen.findByLabelText<HTMLTextAreaElement>("起きていること")).toHaveValue(
      "docker が失敗する",
    );
    expect(screen.getByLabelText<HTMLTextAreaElement>("環境")).toHaveValue("M2 Mac");
    expect(screen.getByLabelText<HTMLTextAreaElement>("試したこと")).toHaveValue("");
    expect(screen.getByLabelText<HTMLTextAreaElement>("詰まっている点")).toHaveValue("原因不明");
    expect(structureQuestionMock).toHaveBeenCalledWith({ session_id: "s1" });
  });

  it("applies the edited fields as a composed draft block", async () => {
    const onApply = vi.fn();
    structureQuestionMock.mockResolvedValue({
      session_id: "s1",
      summary: "起きていること",
      environment: "",
      tried: "",
      blocker: "詰まっている点",
    });
    render(<QuestionStructurePanel sessionId="s1" onApply={onApply} />);

    fireEvent.click(screen.getByRole("button", { name: "AIに質問を整理してもらう" }));
    const env = await screen.findByLabelText<HTMLTextAreaElement>("環境");
    fireEvent.change(env, { target: { value: "Ubuntu 24.04" } });

    fireEvent.click(screen.getByRole("button", { name: "下書きに反映" }));
    expect(onApply).toHaveBeenCalledWith(
      "【起きていること】\n起きていること\n\n【環境】\nUbuntu 24.04\n\n【詰まっている点】\n詰まっている点",
    );
  });

  it("shows a friendly message when the session has no question (404)", async () => {
    const { ApiError } = await import("@/lib/api-client");
    structureQuestionMock.mockRejectedValue(new ApiError(404, "no question"));
    render(<QuestionStructurePanel sessionId="s1" onApply={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "AIに質問を整理してもらう" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "整理できる質問が見つかりませんでした。",
    );
  });

  it("surfaces a retryable error on an unexpected failure", async () => {
    structureQuestionMock.mockRejectedValue(new Error("boom"));
    render(<QuestionStructurePanel sessionId="s1" onApply={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "AIに質問を整理してもらう" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("質問の整理に失敗しました"),
    );
  });

  it("disables the trigger when the panel is disabled", () => {
    render(<QuestionStructurePanel sessionId="s1" disabled onApply={vi.fn()} />);
    expect(screen.getByRole("button", { name: "AIに質問を整理してもらう" })).toBeDisabled();
  });
});
