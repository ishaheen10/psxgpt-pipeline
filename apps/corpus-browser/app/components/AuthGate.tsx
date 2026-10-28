"use client";

import { FormEvent, useState } from "react";

interface Props {
  loading: boolean;
  configured: boolean;
  error: string;
  onGoogle: () => Promise<void>;
  onMagicLink: (email: string) => Promise<void>;
}

export default function AuthGate({
  loading,
  configured,
  error,
  onGoogle,
  onMagicLink,
}: Props) {
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [formError, setFormError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!email.trim()) {
      setFormError("Enter your email.");
      return;
    }
    setSending(true);
    setFormError("");
    try {
      await onMagicLink(email.trim());
      setSent(true);
    } catch (sendError) {
      setFormError(sendError instanceof Error ? sendError.message : "Could not send the sign-in link.");
    } finally {
      setSending(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="auth-brand"><span>L</span><strong>Ledger</strong></div>
        <div className="auth-copy">
          <span>Authoritative corpus browser</span>
          <h1>Search the source.<br />Build the context.</h1>
          <p>
            Find exact pages across company filings, PSX announcements, and economic publications.
            Select the evidence yourself, then compile a source-grounded analyst answer.
          </p>
        </div>

        {loading ? (
          <div className="auth-loading"><i /> Connecting securely…</div>
        ) : !configured ? (
          <div className="auth-error">Authentication is not configured on this deployment.</div>
        ) : (
          <div className="auth-actions">
            <button className="google-sign-in" type="button" onClick={() => void onGoogle()}>
              Continue with Google
            </button>
            <div className="auth-divider"><span /> or use email <span /></div>
            <form onSubmit={submit}>
              <label htmlFor="ledger-email">Email</label>
              <div>
                <input
                  id="ledger-email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="you@example.com"
                  disabled={sending || sent}
                />
                <button type="submit" disabled={sending || sent}>
                  {sending ? "Sending…" : sent ? "Sent" : "Email link"}
                </button>
              </div>
            </form>
            {sent && (
              <div className="auth-success">
                Check your inbox and open the secure link to finish signing in.
              </div>
            )}
            {(formError || error) && <div className="auth-error">{formError || error}</div>}
          </div>
        )}
        <small className="auth-footnote">The same Supabase account used by psxGPT works here.</small>
      </section>
    </main>
  );
}
