import { RetrospectiveForm } from "@/components/RetrospectiveForm";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getTopicsMock = vi.fn();
const postConsultRetrospectiveMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
  getTopics: (...args: unknown[]) => getTopicsMock(...args),
  postConsultRetrospective: (...args: unknown[]) => postConsultRetrospectiveMock(...args),
}));

const TOPICS = ["ネットワーク・VPN", "セキュリティ", "経理・決算"];

function renderForm(overrides: Record<string, unknown> = {}) {
  return render(
    <RetrospectiveForm
      questionId="q_0001"
      responderId="E001"
      responderName="山田 太郎"
      {...overrides}
    />,
  );
}

async function fillRequired() {
  fireEvent.click(await screen.findByRole("button", { name: "ネットワーク・VPN" }));
  fireEvent.change(screen.getByLabelText(/得られた回答/), {
    target: { value: "MTU を下げると直る、という話でした" },
  });
}

beforeEach(() => {
  getTopicsMock.mockReset();
  postConsultRetrospectiveMock.mockReset();
  getTopicsMock.mockResolvedValue(TOPICS);
  postConsultRetrospectiveMock.mockResolvedValue({ status: "recorded", consult_id: 1 });
});

describe("RetrospectiveForm", () => {
  it("offers the topics the backend serves, not a hard-coded list", async () => {
    renderForm();
    for (const topic of TOPICS) {
      expect(await screen.findByRole("button", { name: topic })).toBeTruthy();
    }
  });

  it("cannot be submitted until a topic, an answer and a resolution are present", async () => {
    renderForm();
    await screen.findByRole("button", { name: TOPICS[0] });
    const submit = screen.getByRole("button", { name: /記録する/ });
    expect(submit.hasAttribute("disabled")).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "ネットワーク・VPN" }));
    expect(screen.getByRole("button", { name: /記録する/ }).hasAttribute("disabled")).toBe(true);

    fireEvent.change(screen.getByLabelText(/得られた回答/), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: /記録する/ }).hasAttribute("disabled")).toBe(true);

    fireEvent.change(screen.getByLabelText(/得られた回答/), { target: { value: "本文" } });
    expect(screen.getByRole("button", { name: /記録する/ }).hasAttribute("disabled")).toBe(false);
  });

  it("posts the selected topics, the trimmed answer and the resolution", async () => {
    renderForm();
    await fillRequired();
    fireEvent.click(screen.getByRole("radio", { name: /部分的に解決/ }));
    fireEvent.click(screen.getByRole("button", { name: /記録する/ }));

    await waitFor(() => expect(postConsultRetrospectiveMock).toHaveBeenCalledTimes(1));
    expect(postConsultRetrospectiveMock.mock.calls[0][0]).toEqual({
      question_id: "q_0001",
      responder_id: "E001",
      topics: ["ネットワーク・VPN"],
      asked: null,
      answer_body: "MTU を下げると直る、という話でした",
      resolution: "partial",
    });
  });

  it("defaults the resolution to 解決した and allows several topics", async () => {
    renderForm();
    await fillRequired();
    fireEvent.click(screen.getByRole("button", { name: "セキュリティ" }));
    fireEvent.click(screen.getByRole("button", { name: /記録する/ }));

    await waitFor(() => expect(postConsultRetrospectiveMock).toHaveBeenCalledTimes(1));
    const body = postConsultRetrospectiveMock.mock.calls[0][0];
    expect(body.topics).toEqual(["ネットワーク・VPN", "セキュリティ"]);
    expect(body.resolution).toBe("resolved");
  });

  it("deselects a topic when it is clicked again", async () => {
    renderForm();
    await fillRequired();
    fireEvent.click(screen.getByRole("button", { name: "ネットワーク・VPN" }));
    expect(screen.getByRole("button", { name: /記録する/ }).hasAttribute("disabled")).toBe(true);
  });

  it("shows a thank-you instead of the form once recorded", async () => {
    renderForm();
    await fillRequired();
    fireEvent.click(screen.getByRole("button", { name: /記録する/ }));

    expect(await screen.findByText(/記録しました/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /記録する/ })).toBeNull();
  });

  it("surfaces a failure and keeps the entered text so nothing is lost", async () => {
    postConsultRetrospectiveMock.mockRejectedValue(new Error("boom"));
    renderForm();
    await fillRequired();
    fireEvent.click(screen.getByRole("button", { name: /記録する/ }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect((screen.getByLabelText(/得られた回答/) as HTMLTextAreaElement).value).toBe(
      "MTU を下げると直る、という話でした",
    );
    // Still submittable — a transient failure must not strand the write-up.
    expect(screen.getByRole("button", { name: /記録する/ }).hasAttribute("disabled")).toBe(false);
  });

  it("explains that 解決しなかった is recorded but never counts against the responder", async () => {
    renderForm();
    await screen.findByRole("button", { name: TOPICS[0] });
    fireEvent.click(screen.getByRole("radio", { name: /解決しなかった/ }));
    expect(screen.getByText(/評価を下げることはありません/)).toBeTruthy();
  });

  it("reports a topic-list failure rather than rendering an empty picker", async () => {
    getTopicsMock.mockRejectedValue(new Error("nope"));
    renderForm();
    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});
