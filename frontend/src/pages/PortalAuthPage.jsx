import { useState } from "react";
import { login as adminLogin } from "../admin/adminApi";
import { register, signIn } from "../services/portalApi";

// Candidate sign-in / account creation (e-mail + password). Access is enforced by the backend.
export default function PortalAuthPage({ onSignedIn }) {
  const [role, setRole] = useState("candidate");
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ fullName: "", email: "", phone: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (role === "admin") {
        await adminLogin(form.email.trim(), form.password);
        window.location.assign("/admin");
        return;
      }
      if (mode === "login") await signIn(form.email, form.password);
      else await register(form);
      onSignedIn();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page portal-auth">
      <div className="auth-card">
        <div className="lobby-brand">
          <span className="room-logo">Jobs</span>
          <div>
            <strong>Careers portal</strong>
            <span>Customer support and BPO roles</span>
          </div>
        </div>
        <div className="auth-tabs role-tabs" role="tablist" aria-label="Sign in as">
          <button type="button" className={role === "candidate" ? "active" : ""}
            onClick={() => { setRole("candidate"); setError(""); }}>Candidate</button>
          <button type="button" className={role === "admin" ? "active" : ""}
            onClick={() => { setRole("admin"); setMode("login"); setError(""); }}>Admin / Recruiter</button>
        </div>
        {role === "candidate" && <div className="auth-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={mode === "login"} className={mode === "login" ? "active" : ""}
            onClick={() => { setMode("login"); setError(""); }}>Sign in</button>
          <button type="button" role="tab" aria-selected={mode === "register"} className={mode === "register" ? "active" : ""}
            onClick={() => { setMode("register"); setError(""); }}>Create account</button>
        </div>}
        <form onSubmit={submit} className="auth-form">
          {role === "candidate" && mode === "register" && (
            <label>Full name<input value={form.fullName} onChange={set("fullName")} required minLength={2} autoComplete="name" /></label>
          )}
          <label>E-mail<input type="email" value={form.email} onChange={set("email")} required autoComplete="email" /></label>
          {role === "candidate" && mode === "register" && (
            <label>Mobile number (optional)<input value={form.phone} onChange={set("phone")} placeholder="+91 98765 43210" autoComplete="tel" /></label>
          )}
          <label>Password<input type="password" value={form.password} onChange={set("password")} required minLength={8}
            autoComplete={role === "admin" || mode === "login" ? "current-password" : "new-password"} /></label>
          {role === "candidate" && mode === "register" && <p className="field-hint">At least 8 characters, with a letter and a number.</p>}
          {error && <p className="error" role="alert">{error}</p>}
          <button className="lobby-button" disabled={busy}>{busy ? "Please wait..." : role === "admin" || mode === "login" ? "Sign in" : "Create account"}</button>
        </form>
      </div>
    </div>
  );
}
