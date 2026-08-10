import { useState, type Dispatch, type FormEvent, type SetStateAction } from "react";

import { productProfile } from "../../../../product";
import type { ConnectionSettings, DesktopPlatform } from "../../types";

interface LoginViewProps {
  platform: DesktopPlatform;
  connection: ConnectionSettings;
  busy: boolean;
  error: string | null;
  setConnection: Dispatch<SetStateAction<ConnectionSettings>>;
  onLogin: (email: string, secret: string) => Promise<boolean>;
}

/** Authenticate one Desktop user without retaining their login secret. */
export function LoginView(props: LoginViewProps) {
  const [email, setEmail] = useState("");
  const [secret, setSecret] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (await props.onLogin(email, secret)) setSecret("");
  }

  return (
    <main className={`login-shell platform-${props.platform}`}>
      <section className="login-intro" aria-label={`${productProfile.displayName} sign in`}>
        <div className="login-brand"><span aria-hidden="true">OT</span><strong>{productProfile.displayName}</strong></div>
        <div className="login-intro-copy">
          <p className="login-kicker">Your team’s agent workspace</p>
          <h1>Work through your own trusted identity.</h1>
          <p>Agents, sessions, and runs stay scoped to your account. Your administrator controls the Node and your maximum Agent privilege.</p>
        </div>
        <small>HTTPS is required except for loopback Node URLs. Credentials are verified by the Node and the secret is never saved.</small>
      </section>
      <form className="login-card" onSubmit={(event) => void submit(event)}>
        <header><span>Sign in</span><h2>Connect to a Node</h2><p>Use the account created by your OpenTeamwork administrator.</p></header>
        <label><span>Node URL</span><input required spellCheck={false} inputMode="url" value={props.connection.clientApiBaseUrl} onChange={(event) => props.setConnection((current) => ({ ...current, clientApiBaseUrl: event.target.value, accessToken: "" }))} placeholder="https://team.example.com" /></label>
        <label><span>Email</span><input required type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@company.com" /></label>
        <label><span>Secret</span><input required minLength={8} type="password" autoComplete="current-password" value={secret} onChange={(event) => setSecret(event.target.value)} /></label>
        {props.error ? <p className="login-error" role="alert">{props.error}</p> : null}
        <button className="login-submit" disabled={props.busy || !email.trim() || secret.length < 8} type="submit">{props.busy ? "Signing in…" : "Sign in to OpenTeamwork"}</button>
      </form>
    </main>
  );
}
