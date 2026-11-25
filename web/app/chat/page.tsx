"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AttachmentIn,
  ChatMessage,
  Conversation,
  ModelInfo,
  listConversations,
  listMessages,
  listModels,
  research,
  streamChat,
} from "../lib/api";

export default function ChatPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConv, setActiveConv] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState<string>("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [attachments, setAttachments] = useState<
    { name: string; mime_type: string; data_base64: string }[]
  >([]);
  const [researching, setResearching] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = localStorage.getItem("chatluck_token");
    if (!t) {
      router.replace("/login");
      return;
    }
    setToken(t);
  }, [router]);

  const refreshConversations = useCallback(
    async (t: string) => {
      try {
        setConversations(await listConversations(t));
      } catch {
        /* keep previous list */
      }
    },
    []
  );

  useEffect(() => {
    if (!token) return;
    refreshConversations(token);
    listModels(token)
      .then((ms) => {
        setModels(ms);
        const first = ms.find((m) => m.available);
        if (first) setModel(first.id);
      })
      .catch(() => setStatus("تعذر تحميل النماذج"));
  }, [token, refreshConversations]);

  useEffect(() => {
    if (!token || !activeConv) {
      setMessages([]);
      return;
    }
    listMessages(token, activeConv).then(setMessages).catch(() => {});
  }, [token, activeConv]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function onPickImage(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (!files) return;
    Array.from(files).slice(0, 3).forEach((f) => {
      if (!/^image\/(png|jpeg|webp|gif)$/.test(f.type)) {
        setStatus("صيغة الصورة غير مدعومة (png, jpeg, webp, gif فقط)");
        return;
      }
      if (f.size > 10 * 1024 * 1024) {
        setStatus("الصورة أكبر من 10 ميجا");
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const b64 = String(reader.result).split(",")[1] ?? "";
        setAttachments((a) => [
          ...a,
          { name: f.name, mime_type: f.type, data_base64: b64 },
        ]);
      };
      reader.readAsDataURL(f);
    });
    e.target.value = "";
  }

  async function send() {
    if (!token || !input.trim() || busy) return;
    const text = input.trim();
    const atts: AttachmentIn[] = attachments.map((a) => ({
      mime_type: a.mime_type,
      data_base64: a.data_base64,
    }));
    setInput("");
    setAttachments([]);
    setBusy(true);
    setStatus("");
    const userMsg: ChatMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content: text + (atts.length ? `\n[${atts.length} صورة مرفقة]` : ""),
    };
    const assistantMsg: ChatMessage = {
      id: `local-a-${Date.now()}`,
      role: "assistant",
      content: "",
    };
    setMessages((ms) => [...ms, userMsg, assistantMsg]);
    try {
      await streamChat(token, text, activeConv, model || null, {
        onToken: (content) => {
          setMessages((ms) => {
            const copy = [...ms];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = {
              ...last,
              content: last.content + content,
            };
            return copy;
          });
        },
        onFallback: (from) => setStatus(`المزود ${from} لم يستجب، جاري التبديل…`),
        onError: (error) => setStatus(`خطأ: ${error}`),
        onDone: (provider, m) => {
          setMessages((ms) => {
            const copy = [...ms];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = { ...last, provider, model: m };
            return copy;
          });
          setStatus("");
        },
      }, atts);
      if (!activeConv) {
        // Refresh to pick up the newly created conversation.
        const convs = await listConversations(token);
        setConversations(convs);
        if (convs.length > 0) setActiveConv(convs[0].id);
      } else {
        refreshConversations(token);
      }
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "فشل الإرسال");
      setMessages((ms) => ms.slice(0, -1));
    } finally {
      setBusy(false);
    }
  }

  async function runResearch() {
    if (!token || !input.trim() || researching || busy) return;
    const q = input.trim();
    setInput("");
    setResearching(true);
    setStatus("جاري البحث…");
    const userMsg: ChatMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content: `بحث: ${q}`,
    };
    setMessages((ms) => [...ms, userMsg]);
    try {
      const r = await research(token, q);
      const sourcesText = r.sources
        .map((s, i) => `[${i + 1}] ${s.title} — ${s.url}`)
        .join("\n");
      const assistantMsg: ChatMessage = {
        id: `local-r-${Date.now()}`,
        role: "assistant",
        content: `${r.answer}\n\nالمصادر:\n${sourcesText}`,
        provider: r.provider,
        model: r.model,
      };
      setMessages((ms) => [...ms, assistantMsg]);
      setStatus("");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "فشل البحث");
    } finally {
      setResearching(false);
    }
  }

  function logout() {
    localStorage.removeItem("chatluck_token");
    router.replace("/login");
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <h1>Chat Luck AI</h1>
        <button
          className="conv-item"
          onClick={() => {
            setActiveConv(null);
            setMessages([]);
          }}
        >
          + محادثة جديدة
        </button>
        {conversations.map((c) => (
          <button
            key={c.id}
            className={`conv-item ${c.id === activeConv ? "active" : ""}`}
            onClick={() => setActiveConv(c.id)}
          >
            {c.title}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button className="conv-item" onClick={logout}>
          تسجيل الخروج
        </button>
      </aside>
      <main className="main">
        <div className="topbar">
          <label htmlFor="model">النموذج:</label>
          <select
            id="model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id} disabled={!m.available}>
                {m.id} ({m.provider})
                {m.available ? "" : " — غير متاح"}
              </option>
            ))}
          </select>
          {status && <span className="error">{status}</span>}
        </div>
        <div className="messages">
          {messages.length === 0 && (
            <div className="empty-state">
              <div className="logo">✦</div>
              <div>اسأل أي حاجة، بالعربي أو بأي لغة.</div>
            </div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`msg ${m.role}`}>
              {m.content}
              {m.role === "assistant" && m.provider && (
                <span className="meta">
                  {m.provider} / {m.model}
                </span>
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
        {attachments.length > 0 && (
          <div className="attach-bar">
            {attachments.map((a, i) => (
              <span key={i} className="attach-chip">
                {a.name}
                <button
                  onClick={() =>
                    setAttachments((x) => x.filter((_, j) => j !== i))
                  }
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="composer">
          <input
            ref={fileRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            multiple
            style={{ display: "none" }}
            onChange={onPickImage}
          />
          <button
            className="icon-btn"
            title="إرفاق صورة"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-label="إرفاق">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
            </svg>
          </button>
          <textarea
            rows={2}
            placeholder="اكتب رسالتك…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button onClick={send} disabled={busy || !input.trim()}>
            {busy ? "…" : "أرسل"}
          </button>
          <button
            className="icon-btn"
            title="بحث في الويب"
            onClick={runResearch}
            disabled={researching || busy || !input.trim()}
          >
            {researching ? "…" : "🔍"}
          </button>
        </div>
      </main>
    </div>
  );
}
