"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AgentInfo,
  AttachmentIn,
  ChatMessage,
  Conversation,
  ModelInfo,
  ResearchSource,
  agentCatalog,
  deleteConversation,
  generateImage,
  listConversations,
  listMessages,
  listModels,
  renameConversation,
  research,
  runAgent,
  streamChatAbortable,
} from "../lib/api";

type Mode = "chat" | "research" | "image" | "translate" | "code";

interface UiMessage extends ChatMessage {
  imageUrl?: string;
  sources?: ResearchSource[];
  streaming?: boolean;
}

const TOOLS: { mode: Mode | "agents"; icon: string; name: string; desc: string }[] = [
  { mode: "research", icon: "🔎", name: "بحث الإنترنت", desc: "إجابات بمصادر حقيقية وروابط موثوقة" },
  { mode: "image", icon: "🎨", name: "استوديو الصور", desc: "أنشئ صوراً من وصف نصي بالعربي أو الإنجليزي" },
  { mode: "translate", icon: "🌍", name: "الترجمة الاحترافية", desc: "ترجمة بسياق وبأسلوب رسمي أو طبيعي" },
  { mode: "code", icon: "💻", name: "مساعد البرمجة", desc: "اكتب واشرح وأصلح الكود بأي لغة" },
  { mode: "agents", icon: "🤖", name: "المساعدون المتخصصون", desc: "فريق وكلاء ينفذ مهام مركبة خطوة بخطوة" },
];

const SUGGESTIONS = [
  "اشرحلي الذكاء الاصطناعي ببساطة",
  "اكتبلي خطة تعلم برمجة في ٣ شهور",
  "لخصلي أهم أخبار التكنولوجيا",
  "ساعدني أكتب إيميل رسمي بالإنجليزي",
  "إيه الفرق بين بايثون وجافاسكريبت؟",
  "اقترحلي أفكار مشروع صغير",
];

const MODE_META: Record<Mode, { chip: string; placeholder: string }> = {
  chat: { chip: "", placeholder: "اكتب رسالتك…" },
  research: { chip: "🔎 وضع البحث", placeholder: "اكتب سؤالك وهبحث في الإنترنت وأجيبك بالمصادر…" },
  image: { chip: "🎨 وضع الصور", placeholder: "اوصف الصورة اللي عايزها…" },
  translate: { chip: "🌍 وضع الترجمة", placeholder: "اكتب النص واللغة المطلوبة…" },
  code: { chip: "💻 وضع البرمجة", placeholder: "اشرح المطلوب أو الصق الكود…" },
};

export default function ChatPage() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConv, setActiveConv] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState<string>("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [mode, setMode] = useState<Mode>("chat");
  const [attachments, setAttachments] = useState<
    { name: string; mime_type: string; data_base64: string }[]
  >([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentTask, setAgentTask] = useState("");
  const [agentBusy, setAgentBusy] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const t = localStorage.getItem("chatluck_token");
    if (!t) {
      router.replace("/login");
      return;
    }
    setToken(t);
  }, [router]);

  const refreshConversations = useCallback(async (t: string) => {
    try {
      setConversations(await listConversations(t));
    } catch {
      /* keep previous list */
    }
  }, []);

  useEffect(() => {
    if (!token) return;
    refreshConversations(token);
    listModels(token)
      .then((ms) => {
        setModels(ms.filter((m) => m.available && m.capabilities.includes("chat")));
        const first = ms.find((m) => m.available && m.capabilities.includes("chat"));
        if (first) setModel(first.id);
      })
      .catch(() => setStatus("تعذر تحميل النماذج"));
    agentCatalog(token).then(setAgents).catch(() => {});
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

  function appendMessage(msg: UiMessage) {
    setMessages((ms) => [...ms, msg]);
  }

  function patchLastMessage(patch: Partial<UiMessage>) {
    setMessages((ms) => {
      const copy = [...ms];
      const last = copy[copy.length - 1];
      copy[copy.length - 1] = { ...last, ...patch };
      return copy;
    });
  }

  async function refreshAfterNewConv(t: string, wasNew: boolean) {
    const convs = await listConversations(t);
    setConversations(convs);
    if (wasNew && convs.length > 0) setActiveConv(convs[0].id);
  }

  async function send() {
    if (!token || !input.trim() || busy) return;
    const text = input.trim();
    setInput("");
    setStatus("");

    if (mode === "research") return sendResearch(text);
    if (mode === "image") return sendImage(text);

    let finalText = text;
    if (mode === "translate") {
      finalText = `ترجم النص التالي ترجمة احترافية تحفظ المعنى والسياق (وليست حرفية)، وحدد اللغة الهدف من طلبي أو ترجم للإنجليزية إن لم أحدد:\n\n${text}`;
    } else if (mode === "code") {
      finalText = `بصفتك مساعد برمجة خبير، ساعدني في التالي مع شرح واضح وأكواد جاهزة داخل code blocks:\n\n${text}`;
    }

    const atts: AttachmentIn[] = attachments.map((a) => ({
      mime_type: a.mime_type,
      data_base64: a.data_base64,
    }));
    setAttachments([]);
    setBusy(true);
    const wasNew = !activeConv;
    const userMsg: UiMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content: text + (atts.length ? `\n[${atts.length} صورة مرفقة]` : ""),
    };
    const assistantMsg: UiMessage = {
      id: `local-a-${Date.now()}`,
      role: "assistant",
      content: "",
      streaming: true,
    };
    setMessages((ms) => [...ms, userMsg, assistantMsg]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChatAbortable(
        token,
        finalText,
        activeConv,
        model || null,
        {
          onToken: (content) => {
            setMessages((ms) => {
              const copy = [...ms];
              const last = copy[copy.length - 1];
              copy[copy.length - 1] = { ...last, content: last.content + content };
              return copy;
            });
          },
          onFallback: (from) => setStatus(`المزود ${from} لم يستجب، جاري التبديل…`),
          onError: (error) => setStatus(`خطأ: ${error}`),
          onDone: (provider, m) => {
            patchLastMessage({ provider, model: m, streaming: false });
            setStatus("");
          },
        },
        atts,
        controller.signal
      );
      patchLastMessage({ streaming: false });
      await refreshAfterNewConv(token, wasNew);
    } catch (e) {
      if (controller.signal.aborted) {
        patchLastMessage({ streaming: false });
        setStatus("تم إيقاف التوليد");
      } else {
        setStatus(e instanceof Error ? e.message : "فشل الإرسال");
        setMessages((ms) => ms.slice(0, -1));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  async function sendResearch(q: string) {
    if (!token) return;
    setBusy(true);
    setStatus("جاري البحث في الإنترنت…");
    appendMessage({ id: `local-${Date.now()}`, role: "user", content: q });
    try {
      const r = await research(token, q);
      appendMessage({
        id: `local-r-${Date.now()}`,
        role: "assistant",
        content: r.answer,
        provider: r.provider,
        model: r.model,
        sources: r.sources,
      });
      setStatus("");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "فشل البحث");
    } finally {
      setBusy(false);
    }
  }

  async function sendImage(prompt: string) {
    if (!token) return;
    setBusy(true);
    setStatus("جاري إنشاء الصورة…");
    appendMessage({ id: `local-${Date.now()}`, role: "user", content: `🎨 ${prompt}` });
    try {
      const r = await generateImage(token, prompt);
      appendMessage({
        id: `local-i-${Date.now()}`,
        role: "assistant",
        content: `اتفضل الصورة (${r.provider}):`,
        imageUrl: r.url,
        provider: r.provider,
        model: r.model,
      });
      setStatus("");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "فشل إنشاء الصورة");
    } finally {
      setBusy(false);
    }
  }

  async function sendAgentTask() {
    if (!token || !agentTask.trim() || agentBusy) return;
    const task = agentTask.trim();
    setAgentTask("");
    setAgentsOpen(false);
    setAgentBusy(true);
    setStatus("المساعدون بيشتغلوا على المهمة…");
    appendMessage({ id: `local-${Date.now()}`, role: "user", content: `🤖 مهمة للمساعدين: ${task}` });
    try {
      const r = await runAgent(token, task);
      const lines = r.steps
        .map((s, i) => `**${i + 1}. ${s.agent}** — ${s.status}\n${s.output}`)
        .join("\n\n");
      const blockedNote = r.blocked.length
        ? `\n\n> خطوات محظورة تحتاج موافقتك: ${r.blocked.join("، ")}`
        : "";
      appendMessage({
        id: `local-ag-${Date.now()}`,
        role: "assistant",
        content: `## نتيجة تنفيذ المهمة\n\n${lines || "لم تُنفذ خطوات."}${blockedNote}`,
      });
      setStatus("");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "فشل تشغيل المساعد");
    } finally {
      setAgentBusy(false);
    }
  }

  async function onDeleteConv(id: string) {
    if (!token) return;
    try {
      await deleteConversation(token, id);
      if (activeConv === id) {
        setActiveConv(null);
        setMessages([]);
      }
      refreshConversations(token);
    } catch {
      setStatus("فشل حذف المحادثة");
    }
  }

  async function onRenameConv(id: string, oldTitle: string) {
    if (!token) return;
    const title = window.prompt("اسم المحادثة الجديد:", oldTitle);
    if (!title || !title.trim() || title.trim() === oldTitle) return;
    try {
      await renameConversation(token, id, title.trim());
      refreshConversations(token);
    } catch {
      setStatus("فشل إعادة التسمية");
    }
  }

  function copyMessage(m: UiMessage) {
    navigator.clipboard?.writeText(m.content).then(() => {
      setCopiedId(m.id);
      setTimeout(() => setCopiedId(null), 1500);
    });
  }

  function pickTool(modeOrAgents: Mode | "agents") {
    if (modeOrAgents === "agents") {
      setAgentsOpen(true);
      return;
    }
    setMode(modeOrAgents);
    setSidebarOpen(false);
  }

  function logout() {
    localStorage.removeItem("chatluck_token");
    router.replace("/login");
  }

  const isEmpty = messages.length === 0;

  return (
    <div className="shell">
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <h1>Chat Luck AI</h1>
        <button
          className="new-chat-btn"
          onClick={() => {
            setActiveConv(null);
            setMessages([]);
            setMode("chat");
            setSidebarOpen(false);
          }}
        >
          + محادثة جديدة
        </button>
        {conversations.map((c) => (
          <div
            key={c.id}
            role="button"
            tabIndex={0}
            className={`conv-item ${c.id === activeConv ? "active" : ""}`}
            onClick={() => {
              setActiveConv(c.id);
              setSidebarOpen(false);
            }}
            onKeyDown={(e) => e.key === "Enter" && setActiveConv(c.id)}
          >
            <span className="conv-title">{c.title}</span>
            <span className="conv-actions">
              <button
                title="إعادة تسمية"
                onClick={(e) => {
                  e.stopPropagation();
                  onRenameConv(c.id, c.title);
                }}
              >
                ✏️
              </button>
              <button
                title="حذف"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteConv(c.id);
                }}
              >
                🗑️
              </button>
            </span>
          </div>
        ))}
        <div style={{ flex: 1 }} />
        <button className="logout-btn" onClick={logout}>
          تسجيل الخروج
        </button>
      </aside>

      <main className="main">
        <div className="topbar">
          <button className="menu-btn" onClick={() => setSidebarOpen(!sidebarOpen)}>
            ☰
          </button>
          <label htmlFor="model">النموذج:</label>
          <select
            id="model"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
              </option>
            ))}
          </select>
          {status && <span className="status">{status}</span>}
        </div>

        <div className="messages">
          {isEmpty ? (
            <div className="welcome">
              <div className="logo">✦</div>
              <h2>أهلاً بيك في Chat Luck</h2>
              <p>مساعدك الذكي متعدد النماذج — اسأل، ابحث، ترجم، ابرمج، أو أنشئ صوراً.</p>
              <div className="tools-grid">
                {TOOLS.map((t) => (
                  <button key={t.mode} className="tool-card" onClick={() => pickTool(t.mode)}>
                    <div className="t-icon">{t.icon}</div>
                    <div className="t-name">{t.name}</div>
                    <div className="t-desc">{t.desc}</div>
                  </button>
                ))}
              </div>
              <div className="sugg-label">جرب تسأل:</div>
              <div className="sugg-grid">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    className="sugg-chip"
                    onClick={() => {
                      setInput(s);
                      setMode("chat");
                    }}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <div key={m.id} className={`msg-row ${m.role}`}>
                <div className="avatar">{m.role === "user" ? "أنت" : "✦"}</div>
                <div className="bubble">
                  {m.role === "assistant" ? (
                    <div className={`md ${m.streaming ? "cursor" : ""}`}>
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {m.content || "…"}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    m.content
                  )}
                  {m.imageUrl && (
                    <a href={m.imageUrl} target="_blank" rel="noreferrer">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        className="gen-image"
                        src={m.imageUrl}
                        alt="صورة منشأة بالذكاء الاصطناعي"
                        onError={(e) => {
                          (e.target as HTMLImageElement).style.display = "none";
                        }}
                      />
                    </a>
                  )}
                  {m.sources && m.sources.length > 0 && (
                    <div className="sources">
                      {m.sources.map((s, i) => (
                        <a key={i} className="source-card" href={s.url} target="_blank" rel="noreferrer">
                          <span className="s-title">
                            [{i + 1}] {s.title}
                          </span>
                          <span className="s-url">{s.url}</span>
                        </a>
                      ))}
                    </div>
                  )}
                  {m.role === "assistant" && m.provider && (
                    <span className="meta">
                      {m.provider} / {m.model}
                    </span>
                  )}
                  {m.role === "assistant" && !m.streaming && (
                    <div className="msg-actions">
                      <button onClick={() => copyMessage(m)}>
                        {copiedId === m.id ? "✓ اتنسخ" : "نسخ"}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>

        <div className="composer-zone">
          {mode !== "chat" && (
            <div className="mode-bar">
              <span className="mode-chip">
                {MODE_META[mode].chip}
                <button onClick={() => setMode("chat")}>×</button>
              </span>
            </div>
          )}
          {attachments.length > 0 && (
            <div className="attach-bar">
              {attachments.map((a, i) => (
                <span key={i} className="attach-chip">
                  {a.name}
                  <button onClick={() => setAttachments((x) => x.filter((_, j) => j !== i))}>
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
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-label="إرفاق">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            <textarea
              rows={2}
              placeholder={MODE_META[mode].placeholder}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
            />
            {busy && mode === "chat" ? (
              <button
                className="send-btn stop-btn"
                title="إيقاف التوليد"
                onClick={() => abortRef.current?.abort()}
              >
                ■
              </button>
            ) : (
              <button className="send-btn" onClick={send} disabled={busy || !input.trim()} title="إرسال">
                ➤
              </button>
            )}
          </div>
        </div>
      </main>

      {agentsOpen && (
        <div className="modal-back" onClick={() => setAgentsOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <button className="close-x" onClick={() => setAgentsOpen(false)}>
              ×
            </button>
            <h3>المساعدون المتخصصون</h3>
            <div className="m-sub">
              اكتب مهمة وسيخطط لها المساعد الرئيسي ويوزعها على الوكلاء المتخصصين.
            </div>
            <textarea
              rows={3}
              style={{
                width: "100%",
                background: "var(--panel-2)",
                border: "1px solid var(--hairline)",
                borderRadius: 12,
                color: "var(--ink)",
                padding: 12,
                fontFamily: "inherit",
                fontSize: 14,
                marginBottom: 14,
              }}
              placeholder="مثال: ابحث عن أحدث أخبار الذكاء الاصطناعي وترجمهم ملخصاً بالعربي"
              value={agentTask}
              onChange={(e) => setAgentTask(e.target.value)}
            />
            <button
              className="new-chat-btn"
              style={{ marginBottom: 16 }}
              disabled={agentBusy || !agentTask.trim()}
              onClick={sendAgentTask}
            >
              {agentBusy ? "جاري التنفيذ…" : "نفذ المهمة"}
            </button>
            {agents.map((a) => (
              <div key={a.name} className="agent-item">
                <div className="a-name">
                  {a.name} {a.sensitive ? "🔒" : ""}
                </div>
                <div className="a-desc">{a.description}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
