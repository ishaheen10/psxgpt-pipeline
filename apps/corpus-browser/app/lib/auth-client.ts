"use client";

import { createClient, type SupabaseClient, type User } from "@supabase/supabase-js";
import { useCallback, useEffect, useState } from "react";

interface AuthConfig {
  configured: boolean;
  url?: string;
  anonKey?: string;
}

let clientPromise: Promise<SupabaseClient | null> | null = null;

async function getClient(): Promise<SupabaseClient | null> {
  if (!clientPromise) {
    clientPromise = fetch("/api/auth/config", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) return null;
        const config = await response.json() as AuthConfig;
        if (!config.configured || !config.url || !config.anonKey) return null;
        return createClient(config.url, config.anonKey, {
          auth: {
            persistSession: true,
            autoRefreshToken: true,
            detectSessionInUrl: true,
          },
        });
      })
      .catch(() => null);
  }
  return clientPromise;
}

export async function authenticatedFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const client = await getClient();
  const session = client ? (await client.auth.getSession()).data.session : null;
  const headers = new Headers(init.headers);
  if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
  return fetch(input, { ...init, headers });
}

export function useLedgerAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [configured, setConfigured] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let unsubscribe: (() => void) | undefined;
    void getClient().then(async (client) => {
      if (!active) return;
      if (!client) {
        setConfigured(false);
        setLoading(false);
        return;
      }
      const { data, error: sessionError } = await client.auth.getSession();
      if (!active) return;
      if (sessionError) setError(sessionError.message);
      const initialUser = data.session?.user;
      setUser(initialUser && !initialUser.is_anonymous ? initialUser : null);
      setLoading(false);
      const subscription = client.auth.onAuthStateChange((_event, nextSession) => {
        if (!active) return;
        const nextUser = nextSession?.user;
        setUser(nextUser && !nextUser.is_anonymous ? nextUser : null);
      });
      unsubscribe = () => subscription.data.subscription.unsubscribe();
    });
    return () => {
      active = false;
      unsubscribe?.();
    };
  }, []);

  const signInWithGoogle = useCallback(async () => {
    setError("");
    const client = await getClient();
    if (!client) {
      setError("Authentication is not configured.");
      return;
    }
    const { error: signInError } = await client.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin },
    });
    if (signInError) setError(signInError.message);
  }, []);

  const sendMagicLink = useCallback(async (email: string) => {
    setError("");
    const client = await getClient();
    if (!client) throw new Error("Authentication is not configured.");
    const { error: signInError } = await client.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: window.location.origin },
    });
    if (signInError) {
      setError(signInError.message);
      throw signInError;
    }
  }, []);

  const signOut = useCallback(async () => {
    const client = await getClient();
    await client?.auth.signOut();
    setUser(null);
  }, []);

  return {
    user,
    loading,
    configured,
    error,
    signInWithGoogle,
    sendMagicLink,
    signOut,
  };
}
