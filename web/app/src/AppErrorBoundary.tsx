import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { failed: boolean };

/** Prevent an unexpected view error from leaving users on a blank page. */
export class AppErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Deliberately do not expose stack traces or internal error details to users.
  }

  render(): ReactNode {
    if (this.state.failed) {
      return (
        <main className="auth-shell">
          <section className="auth-card">
            <p className="eyebrow">BloodNet / secure access</p>
            <h1>Something went wrong</h1>
            <p className="auth-copy">Your information is safe. Reload to continue.</p>
            <div className="auth-links">
              <button className="primary" onClick={() => window.location.reload()}>Reload</button>
            </div>
          </section>
        </main>
      );
    }
    return this.props.children;
  }
}
