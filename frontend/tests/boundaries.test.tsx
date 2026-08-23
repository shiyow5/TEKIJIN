import ErrorBoundary from "@/../app/error";
import Loading from "@/../app/loading";
import NotFound from "@/../app/not-found";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

describe("route boundaries (#126)", () => {
  it("loading shows a status placeholder", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("読み込み中…");
  });

  it("not-found offers a way home", () => {
    render(<NotFound />);
    expect(screen.getByRole("heading", { name: "ページが見つかりません" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ホームへ戻る" })).toHaveAttribute("href", "/");
  });

  it("error boundary logs the error, retries via reset, and never leaks the detail", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const reset = vi.fn();
    const secret = new Error("DB password is hunter2");
    render(<ErrorBoundary error={secret} reset={reset} />);

    expect(screen.getByRole("alert")).toHaveTextContent("エラーが発生しました");
    expect(screen.queryByText(/hunter2/)).toBeNull(); // no sensitive leakage to the DOM
    expect(consoleError).toHaveBeenCalledWith(secret); // but logged for diagnosis

    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
