"use client";

/**
 * Login screen (#241). Email + password → bearer token via {@link useAuth}. On
 * success the {@link AuthGate} redirects home (and we replace() as well). Renders
 * without the app header (the shell suppresses chrome on this route).
 */

import { useAuth } from "@/components/AuthProvider";
import { ApiError } from "@/lib/api-client";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

function messageForError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "メールアドレスまたはパスワードが違います。";
    if (error.status === 429) {
      return "ログイン試行が多すぎます。しばらくしてからお試しください。";
    }
  }
  return "ログインに失敗しました。時間をおいて、もう一度お試しください。";
}

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = email.trim() !== "" && password !== "" && !submitting;

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await login(email.trim(), password);
      router.replace("/");
    } catch (err) {
      setError(messageForError(err));
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-margin py-lg">
      <div className="w-full max-w-sm rounded-lg border border-outline-variant bg-surface-container-lowest p-lg shadow-sm">
        <div className="mb-lg flex justify-center">
          <img src="/tekijin-logo.png" alt="TEKIJIN" className="h-12 w-auto" />
        </div>
        <h1 className="mb-md text-center font-bold text-lg text-on-surface">ログイン</h1>

        <form onSubmit={onSubmit} className="flex flex-col gap-md" noValidate>
          <label className="flex flex-col gap-xs text-on-surface-variant text-sm">
            メールアドレス
            <input
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="rounded-md border border-outline bg-surface px-sm py-xs text-base text-on-surface"
              aria-label="メールアドレス"
              required
            />
          </label>
          <label className="flex flex-col gap-xs text-on-surface-variant text-sm">
            パスワード
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="rounded-md border border-outline bg-surface px-sm py-xs text-base text-on-surface"
              aria-label="パスワード"
              required
            />
          </label>

          {error ? (
            <p role="alert" className="text-error text-sm">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded-md bg-primary px-md py-sm font-bold text-on-primary transition-opacity disabled:opacity-50"
          >
            {submitting ? "ログイン中…" : "ログイン"}
          </button>
        </form>
      </div>
    </div>
  );
}
