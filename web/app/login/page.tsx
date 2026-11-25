"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { login, register } from "../lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError("");
    setBusy(true);
    try {
      const token =
        mode === "login"
          ? await login(email, password)
          : await register(email, password, name);
      localStorage.setItem("chatluck_token", token);
      router.replace("/chat");
    } catch (e) {
      setError(e instanceof Error ? e.message : "حدث خطأ");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-card">
      <h1>Chat Luck AI</h1>
      {mode === "register" && (
        <input
          placeholder="الاسم"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      )}
      <input
        placeholder="البريد الإلكتروني"
        type="email"
        dir="ltr"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <input
        placeholder="كلمة السر"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
      />
      {error && <div className="error">{error}</div>}
      <button className="btn" onClick={submit} disabled={busy}>
        {mode === "login" ? "دخول" : "إنشاء حساب"}
      </button>
      <div
        className="switch-link"
        onClick={() => setMode(mode === "login" ? "register" : "login")}
      >
        {mode === "login" ? "ماعندكش حساب؟ سجل دلوقتي" : "عندك حساب؟ ادخل"}
      </div>
    </div>
  );
}
